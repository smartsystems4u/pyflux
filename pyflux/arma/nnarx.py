import sys
if sys.version_info < (3,):
    range = xrange

import numpy as np
import pandas as pd
import scipy.stats as ss
import matplotlib.pyplot as plt
import seaborn as sns

from .. import families as fam
from .. import output as op
from .. import tests as tst
from .. import tsm as tsm
from .. import data_check as dc

from .arma_recursions import arima_recursion

class NNARX(tsm.TSM):
    """ Inherits time series methods from TSM parent class.

    **** NEURAL NETWORK AUTOREGRESSIVE INTEGRATED EXOGENOUS (NNARX) MODELS ****

    Parameters
    ----------
    data : pd.DataFrame or np.ndarray
        Field to specify the univariate time series data that will be used.

    formula : string
        patsy string notation describing the regression

    ar : int
        Field to specify how many AR lags the model will have.

    units : int
        How many units for the neural network

    layers : int
        How many layers for the neural networ

    activation : function
        E.g. np.tanh, np.array (linear)
    """

    def __init__(self, data, formula, ar, units, layers, activation=np.tanh):

        # Initialize TSM object
        super(NNAR, self).__init__('NNAR')

        # Latent Variable information
        self.ar = ar
        self.units = units
        self.layers = layers
        self.activation = activation
        self.model_name = "NNARX(" + str(self.ar) + ")"
        self.z_no = self.ar + 2
        self.max_lag = self.ar
        self._z_hide = 0 # Whether to cutoff latent variables from results table
        self.supported_methods = ["BBVI"]
        self.default_method = "BBVI"
        self.multivariate_model = False

        # Format the data
        self.is_pandas = True # This is compulsory for this model type
        self.data_original = data.copy()
        self.formula = formula
        self.y, self.X = dmatrices(formula, data)
        self.y_name = self.y.design_info.describe()
        self.X_names = self.X.design_info.describe().split(" + ")
        self.y = self.y.astype(np.float) 
        self.X = self.X.astype(np.float) 
        self.z_no = self.X.shape[1]
        self.data_name = self.y_name
        self.y = np.array([self.y]).ravel()
        self.data = self.y.copy()
        self.X = np.array([self.X])[0]
        self.index = data.index

        self._norm_mean = np.mean(self.data)
        self._norm_std = np.std(self.data)  
        self.data_normalized = (self.data - self._norm_mean) / self._norm_std

        self.X = self._ar_matrix()
        self._create_latent_variables()

    def _ar_matrix(self):
        """ Creates Autoregressive matrix

        Returns
        ----------
        X : np.ndarray
            Autoregressive Matrix

        """
        Y = np.array(self.data_normalized[self.max_lag:self.data.shape[0]])
        X = np.ones(Y.shape[0])

        if self.ar != 0:
            for i in range(0, self.ar):
                X = np.vstack((X,self.data_normalized[(self.max_lag-i-1):-i-1]))

        return X

    def _create_latent_variables(self):
        """ Creates the model's latent variables

        Returns
        ----------
        None (changes model attributes)
        """

        # Input layer
        for unit in range(self.units):
            self.latent_variables.add_z('Constant | Layer ' + str(1) + ' | Unit ' + str(unit+1), fam.Cauchy(0,1,transform=None), fam.Normal(0, 3))

            for ar_term in range(self.ar):
                self.latent_variables.add_z('AR' + str(ar_term+1) + ' | Layer ' + str(1) + ' | Unit ' + str(unit+1), fam.Cauchy(0,1,transform=None), fam.Normal(0, 3))

            for z in range(len(self.X_names)):
                self.latent_variables.add_z('Weight ' + self.X_names[z], fam.Cauchy(0, 1, transform=None), fam.Normal(0, 3))

        # Hidden layers
        for layer in range(1, self.layers):
            for unit in range(self.units):
                for weight in range(self.units):
                    self.latent_variables.add_z('Weight ' + str(weight+1) + ' | Layer ' + str(layer+1) + ' | Unit ' + str(unit+1), fam.Cauchy(0,1,transform=None), fam.Normal(0, 3))

        # Output layer
        for weight in range(self.units):
            self.latent_variables.add_z('Output Weight ' + str(weight+1), fam.Cauchy(0,1,transform=None), fam.Normal(0, 3))

        self.latent_variables.add_z('Sigma', fam.Flat(transform='exp'), fam.Normal(0, 3))

        # Initialize with random weights
        for var_no in range(len(self.latent_variables.z_list)-1):
            self.latent_variables.z_list[var_no].start = np.random.normal()

    def _model(self, beta):
        """ Creates the structure of the model (model matrices etc)

        Parameters
        ----------
        beta : np.ndarray
            Contains untransformed starting values for the latent variables

        Returns
        ----------
        mu : np.ndarray
            Contains the predicted values (location) for the time series

        Y : np.ndarray
            Contains the length-adjusted time series (accounting for lags)
        """     

        Y = np.array(self.data[self.max_lag:])

        # Transform latent variables
        z = np.array([self.latent_variables.z_list[k].prior.transform(beta[k]) for k in range(beta.shape[0])])

        # Input layer
        first_layer_output = np.zeros((self.X.shape[1], self.units))
        for unit in range(self.units):
            first_layer_output[:,unit] = self.activation(np.matmul(np.transpose(self.X), z[unit*(self.ar+1):((unit+1)*(self.ar+1))]))

        params_used = ((self.units)*(self.ar+1))

        # Hidden layers
        hidden_layer_output = np.zeros((self.X.shape[1], self.units, self.layers-1))
        for layer in range(1, self.layers):
            for unit in range(self.units):
                if layer == 1:
                    hidden_layer_output[:, unit,layer-1] = self.activation(np.matmul(first_layer_output,
                        z[params_used+unit*(self.units)+((layer-1)*self.units**2):((params_used+(unit+1)*self.units)+((layer-1)*self.units**2))]))
                else:
                    hidden_layer_output[:, unit,layer-1] = self.activation(np.matmul(hidden_layer_output[:,:,layer-1],
                        z[params_used+unit*(self.units)+((layer-1)*self.units**2):((params_used+(unit+1)*self.units)+((layer-1)*self.units**2))]))

        params_used = params_used + (self.layers-1)*self.units**2

        # Output layer
        if self.layers == 1:
            mu = np.matmul(first_layer_output, z[params_used:params_used+self.units])
        else:
            mu = np.matmul(hidden_layer_output[:,:,-1], z[params_used:params_used+self.units])

        return mu, Y 


    def predict_new(self, X, z):

        first_layer_output = np.zeros(self.units)
        
        for unit in range(self.units):
            first_layer_output[unit] = self.activation(np.matmul(np.transpose(X), z[unit*(self.ar+1):((unit+1)*(self.ar+1))]))

        params_used = ((self.units)*(self.ar+1))

        # Hidden layers
        hidden_layer_output = np.zeros((self.units, self.layers-1))
        for layer in range(1, self.layers):
            for unit in range(self.units):
                if layer == 1:
                    hidden_layer_output[unit,layer-1] = self.activation(np.matmul(first_layer_output,
                        z[params_used+unit*(self.units)+((layer-1)*self.units**2):((params_used+(unit+1)*self.units)+((layer-1)*self.units**2))]))
                else:
                    hidden_layer_output[unit,layer-1] = self.activation(np.matmul(hidden_layer_output[:,layer-1],
                        z[params_used+unit*(self.units)+((layer-1)*self.units**2):((params_used+(unit+1)*self.units)+((layer-1)*self.units**2))]))

        params_used = params_used + (self.layers-1)*self.units**2

        # Output layer
        if self.layers == 1:
            mu = np.matmul(first_layer_output, z[params_used:params_used+self.units])
        else:
            mu = np.matmul(hidden_layer_output[:,-1], z[params_used:params_used+self.units])

        return mu

    def _mean_prediction(self, mu, Y, h, t_z):
        """ Creates a h-step ahead mean prediction

        Parameters
        ----------
        mu : np.ndarray
            The past predicted values

        Y : np.ndarray
            The past data

        h : int
            How many steps ahead for the prediction

        t_z : np.ndarray
            A vector of (transformed) latent variables

        Returns
        ----------
        h-length vector of mean predictions
        """     

        # Create arrays to iteratre over
        Y_exp = Y.copy()

        # Loop over h time periods          
        for t in range(0,h):

            if self.ar != 0:
                Y_exp_normalized = (Y_exp[-self.ar:][::-1] - self._norm_mean) / self._norm_std
                new_value = self.predict_new(np.append(1.0, Y_exp_normalized), self.latent_variables.get_z_values())

            else:  
                new_value = self.predict_new(np.array([1.0]), self.latent_variables.get_z_values())

            Y_exp = np.append(Y_exp, [new_value])

        return Y_exp

    def _sim_prediction(self, mu, Y, h, t_z, simulations):
        """ Simulates a h-step ahead mean prediction

        Parameters
        ----------
        mu : np.ndarray
            The past predicted values

        Y : np.ndarray
            The past data

        h : int
            How many steps ahead for the prediction

        t_params : np.ndarray
            A vector of (transformed) latent variables

        simulations : int
            How many simulations to perform

        Returns
        ----------
        Matrix of simulations
        """     

        sim_vector = np.zeros([simulations,h])

        for n in range(0, simulations):
            # Create arrays to iteratre over        
            Y_exp = Y.copy()

            # Loop over h time periods          
            for t in range(0,h):

                if self.ar != 0:
                    Y_exp_normalized = (Y_exp[-self.ar:][::-1] - self._norm_mean) / self._norm_std
                    new_value = self.predict_new(np.append(1.0, Y_exp_normalized), self.latent_variables.get_z_values())

                else:
                    new_value = self.predict_new(np.array([1.0]), self.latent_variables.get_z_values())

                new_value += np.random.randn(1)*t_z[-1]

                Y_exp = np.append(Y_exp, [new_value])

            sim_vector[n] = Y_exp[-h:]

        return np.transpose(sim_vector)

    def _summarize_simulations(self, mean_values, sim_vector, date_index, h, past_values):
        """ Produces simulation forecasted values and prediction intervals

        Parameters
        ----------
        mean_values : np.ndarray
            Mean predictions for h-step ahead forecasts

        sim_vector : np.ndarray
            N simulated predictions for h-step ahead forecasts

        date_index : pd.DateIndex or np.ndarray
            Date index for the simulations

        h : int
            How many steps ahead are forecast

        past_values : int
            How many past observations to include in the forecast plot

        intervals : boolean
            Would you like to show prediction intervals for the forecast?
        """         

        error_bars = []
        for pre in range(5,100,5):
            error_bars.append(np.insert([np.percentile(i,pre) for i in sim_vector] - mean_values[-h:],0,0))
        forecasted_values = mean_values[-h-1:]
        plot_values = mean_values[-h-past_values:]
        plot_index = date_index[-h-past_values:]
        return error_bars, forecasted_values, plot_values, plot_index
        
    def neg_loglik_general(self, beta):
        """ Calculates the negative log-likelihood of the model

        Parameters
        ----------
        beta : np.ndarray
            Contains untransformed starting values for latent variables

        Returns
        ----------
        The negative logliklihood of the model
        """     

        mu, Y = self._model(beta)
        return -np.sum(ss.norm.logpdf(Y, loc=mu, scale=self.latent_variables.z_list[-1].prior.transform(beta[-1])))

    def normal_neg_loglik(self, beta):
        """ Creates the negative log-likelihood of the model

        Parameters
        ----------
        beta : np.ndarray
            Contains untransformed starting values for latent variables

        Returns
        ----------
        The negative logliklihood of the model
        """     

        mu, Y = self._model(beta)
        return -np.sum(ss.norm.logpdf(Y, loc=mu, scale=self.latent_variables.z_list[-1].prior.transform(beta[-1])))

    def plot_fit(self, **kwargs):
        """ 
        Plots the fit of the model against the data
        """

        figsize = kwargs.get('figsize',(10,7))
        plt.figure(figsize=figsize)
        date_index = self.index[self.ar:self.data.shape[0]]
        mu, Y = self._model(self.latent_variables.get_z_values())
        plt.plot(date_index,Y,label='Data')
        plt.plot(date_index,mu,label='Filter',c='black')
        plt.title(self.data_name)
        plt.legend(loc=2)   
        plt.show()          

    def plot_predict(self, h=5, past_values=20, intervals=True, **kwargs):
        """ Plots forecasts with the estimated model

        Parameters
        ----------
        h : int (default : 5)
            How many steps ahead would you like to forecast?

        past_values : int (default : 20)
            How many past observations to show on the forecast graph?

        intervals : boolean
            Would you like to show prediction intervals for the forecast?

        Returns
        ----------
        - Plot of the forecast
        """     

        figsize = kwargs.get('figsize',(10,7))

        if self.latent_variables.estimated is False:
            raise Exception("No latent variables estimated!")
        else:
            # Retrieve data, dates and (transformed) latent variables
            mu, Y = self._model(self.latent_variables.get_z_values())         
            date_index = self.shift_dates(h)
            t_z = self.transform_z()

            # Get mean prediction and simulations (for errors)
            mean_values = self._mean_prediction(mu, Y, h, t_z)
            if intervals is True:
                sim_values = self._sim_prediction(mu, Y, h, t_z, 15000)
            else:
                sim_values = self._sim_prediction(mu, Y, h, t_z, 2)
            error_bars, forecasted_values, plot_values, plot_index = self._summarize_simulations(mean_values, sim_values, date_index, h, past_values)

            plt.figure(figsize=figsize)
            if intervals is True:
                alpha =[0.15*i/float(100) for i in range(50,12,-2)]
                for count, pre in enumerate(error_bars):
                    plt.fill_between(date_index[-h-1:], forecasted_values-pre, forecasted_values+pre,alpha=alpha[count])            
            plt.plot(plot_index,plot_values)
            plt.title("Forecast for " + self.data_name)
            plt.xlabel("Time")
            plt.ylabel(self.data_name)
            plt.show()

    def predict_is(self, h=5, fit_once=True):
        """ Makes dynamic out-of-sample predictions with the estimated model on in-sample data

        Parameters
        ----------
        h : int (default : 5)
            How many steps would you like to forecast?

        fit_once : boolean
            (default: True) Fits only once before the in-sample prediction; if False, fits after every new datapoint

        Returns
        ----------
        - pd.DataFrame with predicted values
        """     
        predictions = []

        for t in range(0,h):
            x = NNAR(ar=self.ar, formula=self.formula, units=self.units, 
                layers=self.layers, activation=self.activation, data=self.data_original[:-h+t])
            if fit_once is False:
                x.fit(printer=False)
            if t == 0:
                if fit_once is True:
                    x.fit(printer=False)
                    saved_lvs = x.latent_variables
                predictions = x.predict(1)
            else:
                if fit_once is True:
                    x.latent_variables = saved_lvs
                predictions = pd.concat([predictions,x.predict(1)])
        
        predictions.rename(columns={0:self.data_name}, inplace=True)
        predictions.index = self.index[-h:]

        return predictions

    def plot_predict_is(self, h=5, fit_once=True, **kwargs):
        """ Plots forecasts with the estimated model against data (Simulated prediction with data)

        Parameters
        ----------
        h : int (default : 5)
            How many steps to forecast

        fit_once : boolean
            (default: True) Fits only once before the in-sample prediction; if False, fits after every new datapoint

        Returns
        ----------
        - Plot of the forecast against data 
        """     

        figsize = kwargs.get('figsize',(10,7))
        plt.figure(figsize=figsize)
        predictions = self.predict_is(h, fit_once=fit_once)
        data = self.data[-h:]
        plt.plot(predictions.index, data, label='Data')
        plt.plot(predictions.index, predictions, label='Predictions', c='black')
        plt.title(self.data_name)
        plt.legend(loc=2)   
        plt.show()          

    def predict(self, h=5):
        """ Makes forecast with the estimated model

        Parameters
        ----------
        h : int (default : 5)
            How many steps ahead would you like to forecast?

        Returns
        ----------
        - pd.DataFrame with predicted values
        """     

        if self.latent_variables.estimated is False:
            raise Exception("No latent variables estimated!")
        else:
            mu, Y = self._model(self.latent_variables.get_z_values())         
            date_index = self.shift_dates(h)
            t_z = self.transform_z()
            mean_values = self._mean_prediction(mu, Y, h, t_z)
            forecasted_values = mean_values[-h:]
            result = pd.DataFrame(forecasted_values)
            result.rename(columns={0:self.data_name}, inplace=True)
            result.index = date_index[-h:]

            return result