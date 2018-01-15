import collections
import os
import sys

from copy import deepcopy

import matplotlib.pyplot as plt
import numpy as np
from astromodels import Parameter
from cthreeML.pyModelInterfaceCache import pyToCppModelInterfaceCache
from hawc import liff_3ML
from matplotlib import gridspec
from threeML.exceptions.custom_exceptions import custom_warnings

from threeML.io.file_utils import file_existing_and_readable, sanitize_filename
from threeML.plugin_prototype import PluginPrototype

defaultMinChannel = 0
defaultMaxChannel = 9

__instrument_name = "HAWC"


class NoFullSky(RuntimeWarning):

    pass


class HAWCLike(PluginPrototype):

    def __init__(self, name, maptree, response, n_transits=None, fullsky=False):

        # This controls if the likeHAWC class should load the entire
        # map or just a small disc around a source (faster).
        # Default is the latter, which is way faster. LIFF will decide
        # autonomously which ROI to use depending on the source model

        self._fullsky = bool(fullsky)

        # Sanitize files in input (expand variables and so on)

        self._maptree = os.path.abspath(sanitize_filename(maptree))

        self._response = os.path.abspath(sanitize_filename(response))

        # Check that they exists and can be read

        if not file_existing_and_readable(self._maptree):
            raise IOError("MapTree %s does not exist or is not readable" % maptree)

        if not file_existing_and_readable(self._response):
            raise IOError("Response %s does not exist or is not readable" % response)

        # Post-pone the creation of the LIFF instance to when
        # we have the likelihood model

        self._instanced = False

        # Number of transits
        if n_transits is not None:

            self._n_transits = float(n_transits)

        else:

            self._n_transits = None

        # Default list of bins
        
        self._bin_list = self._min_and_max_to_list(defaultMinChannel,
                                                   defaultMaxChannel)

        # By default the fit of the CommonNorm is deactivated
        # NOTE: this flag sets the internal common norm minimization of LiFF, not
        # the common norm as nuisance parameter (which is controlled by activate_CommonNorm() and
        # deactivate_CommonNorm()
        self._fit_commonNorm = False

        # This is to keep track of whether the user defined a ROI or not

        self._roi_ra = None
        self._roi_fits = None
        self._roi_galactic = False

        # Create the dictionary of nuisance parameters

        self._nuisance_parameters = collections.OrderedDict()

        param_name = "%s_ComNorm" % name

        self._nuisance_parameters[param_name] = Parameter(param_name, 1.0, min_value=0.5, max_value=1.5, delta=0.01)
        self._nuisance_parameters[param_name].fix = True

        super(HAWCLike, self).__init__(name, self._nuisance_parameters)

    @staticmethod
    def _min_and_max_to_list(min_channel, max_channel):
        
        return [str(n) for n in xrange(min_channel, max_channel + 1)]

    def _check_fullsky(self, method_name):

        if not self._fullsky:

            custom_warnings.warn("Attempting to use method %s, but fullsky=False during construction. "
                                 "This might fail. If it does, specify `fullsky=True` when instancing "
                                 "the plugin and try again." % method_name,
                                 NoFullSky)


    def set_ROI(self, ra, dec, radius, fixed_ROI=False, galactic=False):

        self._check_fullsky("set_ROI")

        self._roi_ra = ra
        self._roi_dec = dec

        self._roi_radius = radius

        self._fixed_ROI = fixed_ROI
        self._roi_galactic = galactic

    def set_strip_ROI(self, rastart, rastop, decstart, decstop, fixed_ROI=False, galactic=False):

        self._check_fullsky("set_ROI")

        self._roi_ra = [rastart, rastop]
        self._roi_dec = [decstart, decstop]

        self._fixed_ROI = fixed_ROI
        self._roi_galactic = galactic

    def set_polygon_ROI(self, ralist, declist, fixed_ROI=False, galactic=False):

        self._check_fullsky("set_ROI")

        self._roi_ra = ralist
        self._roi_dec = declist

        self._fixed_ROI = fixed_ROI
        self._roi_galactic = galactic

    def set_template_ROI(self, fitsname, threshold, fixed_ROI=False):

        self._check_fullsky("set_ROI")

        self._roi_ra = None

        self._roi_fits = fitsname
        self._roi_threshold = threshold

        self._fixed_ROI = fixed_ROI
        self._roi_galactic = False

    def __getstate__(self):

        # This method is used by pickle before attempting to pickle the class

        # Return only the objects needed to recreate the class
        # IN particular, we do NOT return the theLikeHAWC class,
        # which is not pickleable. It will instead be recreated
        # on the other side

        d = {}

        d['name'] = self.name
        d['maptree'] = self._maptree
        d['response'] = self._response
        d['model'] = self._model
        d['n_transits'] = self._n_transits
        d['bin_list'] = self._bin_list
        d['roi_ra'] = self._roi_ra

        if self._roi_ra is not None:
            d['roi_dec'] = self._roi_dec
            d['roi_radius'] = self._roi_radius

        return d

    def __setstate__(self, state):

        # This is used by pickle to recreate the class on the remote
        # side
        name = state['name']
        maptree = state['maptree']
        response = state['response']
        ntransits = state['n_transits']

        self._n_transits = ntransits

        # Now report the class to its state

        self.__init__(name, maptree, response)

        if state['roi_ra'] is not None:
            self.set_ROI(state['roi_ra'], state['roi_dec'], state['roi_radius'], state['fixedROI'])

        self.set_bin_list(state['bin_list'])

        self.set_model(state['model'])
        
    def set_bin_list(self, bin_list):
        
        self._bin_list = bin_list

        if self._instanced:
            sys.stderr.write("Since the plugins was already used before, the change in active measurements" +
                             "will not be effective until you create a new JointLikelihood or Bayesian" +
                             "instance")

    def set_active_measurements(self, minChannel, maxChannel):
    
        self.set_bin_list(self._min_and_max_to_list(minChannel, maxChannel))

    def set_model(self, likelihood_model_instance):
        """
        Set the model to be used in the joint minimization. Must be a LikelihoodModel instance.
        """

        # Instance the python - C++ bridge

        self._model = likelihood_model_instance

        self._pymodel = pyToCppModelInterfaceCache()

        # Set boundaries for extended source
        # NOTE: we assume that these boundaries do not change during the fit

        for id in range(self._model.get_number_of_extended_sources()):

            lon_min, lon_max, lat_min, lat_max = self._model.get_extended_source_boundaries(id)

            self._pymodel.setExtSourceBoundaries(id, lon_min, lon_max, lat_min, lat_max)

        # Set positions for point source
        # NOTE: this should not change so much that the response is not valid anymore

        n_point_sources = self._model.get_number_of_point_sources()

        for id in range(n_point_sources):

            this_ra, this_dec = self._model.get_point_source_position(id)

            self._pymodel.setPtsSourcePosition(id, this_ra, this_dec)

        # Now init the HAWC LIFF software

        try:

            # Load all sky
            # (ROI will be defined later)

            if self._n_transits is None:

                self._theLikeHAWC = liff_3ML.LikeHAWC(self._maptree,
                                                      self._response,
                                                      self._pymodel,
                                                      self._bin_list,
                                                      self._fullsky)

            else:

                self._theLikeHAWC = liff_3ML.LikeHAWC(self._maptree,
                                                      self._n_transits,
                                                      self._response,
                                                      self._pymodel,
                                                      self._bin_list,
                                                      self._fullsky)

        except:

            print("Could not instance the LikeHAWC class from LIFF. " +
                  "Check that HAWC software is working")

            raise

        else:

            self._instanced = True

        # If fullsky=True, the user *must* use one of the set_ROI methods

        if self._fullsky:

            if self._roi_ra is None and self._roi_fits is None:

                raise RuntimeError("You have to define a ROI with the setROI method")

        # Now if an ROI is set, try to use it

        if self._roi_ra is not None:

            if not isinstance(self._roi_ra, list):

                self._theLikeHAWC.SetROI(self._roi_ra, self._roi_dec, self._roi_radius, self._fixed_ROI,
                                         self._roi_galactic)

            elif len(self._roi_ra) == 2:

                self._theLikeHAWC.SetROI(self._roi_ra[0], self._roi_ra[1], self._roi_dec[0], self._roi_dec[1],
                                         self._fixed_ROI, self._roi_galactic)

            elif len(self._roi_ra) > 2:

                self._theLikeHAWC.SetROI(self._roi_ra, self._roi_dec, self._fixed_ROI, self._roi_galactic)

            else:

                raise RuntimeError(
                    "Only one point is found, use set_ROI(float ra, float dec, float radius, bool fixedROI, bool galactic).")

        elif self._roi_fits is not None:

            self._theLikeHAWC.SetROI(self._roi_fits, self._roi_threshold, self._fixed_ROI)

        # Now set a callback in the CommonNorm parameter, so that if the user or the fit
        # engine or the Bayesian sampler change the CommonNorm value, the change will be
        # propagated to the LikeHAWC instance

        self._nuisance_parameters.values()[0].add_callback(self._CommonNormCallback)

        # Update to start the computation of positions and energies inside LiFF

        self._theLikeHAWC.UpdateSources()

        # Get the energies needed by LiFF (the same for all sources)
        # (note that the output is in MeV, while we need keV)

        self._energies = np.array(self._theLikeHAWC.GetEnergies(False)) * 1000.0

    def _CommonNormCallback(self, commonNorm_parameter):

        self._theLikeHAWC.SetCommonNorm(commonNorm_parameter.value)

    def activate_CommonNorm(self):

        self._nuisance_parameters.values()[0].free = True

    def deactivate_CommonNorm(self):

        self._nuisance_parameters.values()[0].free = False

    def _fill_model_cache(self):

        n_extended = self._model.get_number_of_extended_sources()

        # Pre-compute all the model

        for id in range(n_extended):

            # Get the positions for this extended source
            positions = np.array(self._theLikeHAWC.GetPositions(id, False), order='C')

            ras = positions[:, 0]
            decs = positions[:, 1]

            # Get the energies for this extended source
            # We need to multiply by 1000 because the cube is in "per keV" while
            # LiFF needs "per MeV"

            cube = self._model.get_extended_source_fluxes(id, ras, decs, self._energies) * 1000.0

            # Make sure that cube is in C order (and not fortran order), otherwise
            # the cache will silently fail!

            if not cube.flags.c_contiguous:

                cube = np.array(cube, order='C')

            if not ras.flags.c_contiguous:

                ras = np.array(ras, order='C')

            if not decs.flags.c_contiguous:

                decs = np.array(decs, order='C')

            assert ras.flags.c_contiguous
            assert decs.flags.c_contiguous
            assert cube.flags.c_contiguous

            self._pymodel.setExtSourceCube(id, cube, ras, decs)

        n_point_sources = self._model.get_number_of_point_sources()

        for id in range(n_point_sources):

            # The 1000.0 factor is due to the fact that this diff. flux here is in
            # 1 / (kev cm2 s) while LiFF needs it in 1 / (MeV cm2 s)

            this_spectrum = self._model.get_point_source_fluxes(id, self._energies, tag=self._tag) * 1000.0

            this_ra, this_dec = self._model.get_point_source_position(id)

            self._pymodel.setPtsSourcePosition(id, this_ra, this_dec)

            if not this_spectrum.flags.c_contiguous:

                this_spectrum = np.array(this_spectrum, order='C')

            assert this_spectrum.flags.c_contiguous

            self._pymodel.setPtsSourceSpectrum(id, this_spectrum)

    def get_log_like(self):

        '''
        Return the value of the log-likelihood with the current values for the
        parameters
        '''

        self._fill_model_cache()

        logL = self._theLikeHAWC.getLogLike(self._fit_commonNorm)

        return logL

    def calc_TS(self):

        '''
        Return the value of the log-likelihood test statistic, defined as
        2*[log(LL_model) - log(LL_bkg)]
        '''

        self._fill_model_cache()

        TS = self._theLikeHAWC.calcTS(self._fit_commonNorm)

        return TS

    def get_nuisance_parameters(self):
        '''
        Return a list of nuisance parameters. Return an empty list if there
        are no nuisance parameters
        '''

        return self._nuisance_parameters.keys()

    def inner_fit(self):

        self._theLikeHAWC.SetBackgroundNormFree(self._fit_commonNorm)

        logL = self.get_log_like()

        self._nuisance_parameters.values()[0].value = self._theLikeHAWC.CommonNorm()

        return logL

    def display(self, radius=0.5, pulls=False):

        """
        Plot model&data/residuals vs HAWC analysis bins for all point sources in the model.

        :param radius: Radius of disk around each source over which model/data are evaluated. Default 0.5.
        :param pulls: Plot pulls ( [excess-model]/uncertainty ) rather than fractional difference ( [excess-model]/model )
                      in lower panel (default: False).
        :return: list of figures (one plot per point source).
        """

        figs = []

        nsrc = self._model.get_number_of_point_sources()

        for srcid in range(nsrc):
            ra, dec = self._model.get_point_source_position(srcid)
            figs.append( self.display_residuals_at_position(ra, dec, radius, pulls) )

        return figs

    def display_residuals_at_position(self, ra, dec, radius=0.5, pulls=False):

        """
        Plot model&data/residuals vs HAWC analysis bins at arbitrary location.
    
        :param ra: R.A. of center of disk (in J2000) over which model/data are evaluated.
        :param dec: Declination of center of disk.
        :param radius: Radius of disk (in degrees). Default 0.5.
        :param pulls: Plot pulls ( [excess-model]/uncertainty ) rather than fractional difference ( [excess-model]/model )
                      in lower panel (default: False).
        :return: matplotlib-type figure.
        """

        model = np.array(self._theLikeHAWC.GetTopHatExpectedExcesses(ra, dec, radius))

        signal = np.array(self._theLikeHAWC.GetTopHatExcesses(ra, dec, radius))

        bkg = np.array(self._theLikeHAWC.GetTopHatBackgrounds(ra, dec, radius))

        total = signal + bkg
            
        error = np.sqrt(total)

        fig = plt.figure()

        gs = gridspec.GridSpec(2, 1, height_ratios=[2, 1])
        gs.update(hspace=0)

        sub = plt.subplot(gs[0])

        n_bins    = len(self._bin_list)
        bin_index = np.arange(n_bins)

        sub.errorbar(bin_index, total, yerr=error, capsize=0,
                         color='black', label='Observation', fmt='.')

        sub.plot(bin_index, model + bkg, label='Model + bkg')

        plt.legend(bbox_to_anchor=(1.0, 1.0), loc="upper right",
                       numpoints=1)

        # Residuals

        sub1 = plt.subplot(gs[1])

        # Using model variance to account for low statistic

        resid = (signal - model) / (error if pulls else model)

        sub1.axhline(0, linestyle='--')

        sub1.errorbar(
            bin_index, resid,
            yerr=np.zeros(error.shape) if pulls else error / model,
            capsize=0, fmt='.'
        )

        x_limits = [-0.5, n_bins - 0.5]
        sub.set_xlim(x_limits)

        sub.set_yscale("log", nonposy='clip')

        sub.set_ylabel("Counts per bin")

        # sub1.set_xscale("log")

        sub1.set_xlabel("Analysis bin")

        sub1.set_ylabel(r"$\frac{{excess - "
                            "mod.}}{{{}.}}$".format("err" if pulls else "mod"))

        sub1.set_xlim(x_limits)

        sub.set_xticks([])
        sub1.set_xticks(bin_index)
        sub1.set_xticklabels(self._bin_list)

        return fig

    def get_number_of_data_points( self ):
        """
        Number of data point = number of pixels.
        Implemented in liff as the number of pixels in the ROI per analysis bin.
        """        
        pixels_per_bin =  np.array( self._theLikeHAWC.GetNumberOfPixels() )
        return int(np.sum( pixels_per_bin ))



    def get_radial_profile(self, ra, dec, bin_list = None, max_radius=3.0, n_radial_bins = 30, model_to_subtract = None ):

        """
        Calculates radial profiles of data - background & model.
    
        :param ra: R.A. of origin for radial profile.
        :param dec: Declination of origin of radial profile.
        :param bin_list: List of analysis bins over which to average; if None, use HAWC default (bins 4-9).
        :param max_radius: Radius up to which the radial profile is evaluated; also used as the radius for the disk to calculate the gamma/hadron weights. Default: 3.0
        :param n_radial_bins: Number of bins for the radial profile. Default: 30.
        :param model_to_subtract: Another model that is to be subtracted from the data excess. Default: None.
        
        :return: np arrays with the radii, model profile, data profile, data uncertainty, list of analysis bins used.
        """

        self._fill_model_cache()
        self.calc_TS()

        #default is to use bins 4-9
        if bin_list is None:
          bin_list = self._min_and_max_to_list( 4, 10 ) 
                
        #Need to make sure we don't try to use bins that we don't have data etc. for.
        good_bins = [bin in bin_list for bin in self._bin_list]

        list_of_bin_names = set(bin_list) & set(self._bin_list)


        delta_r = 1.0*max_radius / n_radial_bins 
        radii = np.array([ delta_r * (i+0.5) for i in range(0,n_radial_bins) ])
        

        # Use GetTopHatAreas to get the area of all pixels in a given circle.
        # The area of each ring is then given by the differnence between two subseqent circle areas.
        area = np.array( [self._theLikeHAWC.GetTopHatAreas(ra, dec, r+0.5*delta_r) for r in radii ] )
        area[1:] -= area[:-1] #convert to ring area 
        area = area*(np.pi/180.)**2 #convert to sr
        
        model = np.array( [self._theLikeHAWC.GetTopHatExpectedExcesses(ra, dec, r+0.5*delta_r) for r in radii ] )
        model[1:] -= model[:-1] #convert 'top hat' excesses into 'ring' excesses.

        signal = np.array( [self._theLikeHAWC.GetTopHatExcesses(ra, dec, r+0.5*delta_r) for r in radii ] )
        signal[1:] -= signal[:-1]

        bkg = np.array( [self._theLikeHAWC.GetTopHatBackgrounds(ra, dec, r+0.5*delta_r) for r in radii ])
        bkg[1:] -= bkg[:-1]

        counts = signal + bkg

        if model_to_subtract is not None:
          this_model = deepcopy(self._model)
          self.set_model( model_to_subtract )
          self._fill_model_cache()
          self.calc_TS()
          model_subtract = np.array( [self._theLikeHAWC.GetTopHatExpectedExcesses(ra, dec, r+0.5*delta_r) for r in radii ] )
          model_subtract[1:] -= model_subtract[:-1]
          signal -= model_subtract
          self.set_model(this_model)
          self._fill_model_cache()
          self.calc_TS()
           
        # weights are calculated as expected number of gamma-rays / number of background counts.
        # here, use max_radius to evaluate the number of gamma-rays/bkg counts.
        # The weights do not depend on the radius, but fill a matrix anyway so there's no confusion when multiplying them to the data later.
        # weight is normalized (sum of weights over the bins = 1).
        
        total_model = np.array( self._theLikeHAWC.GetTopHatExpectedExcesses(ra, dec, max_radius) )[good_bins]
        total_excess = np.array( self._theLikeHAWC.GetTopHatExcesses(ra, dec, max_radius) )[good_bins]
        total_bkg = np.array( self._theLikeHAWC.GetTopHatBackgrounds(ra, dec, max_radius) )[good_bins]
        w=np.divide( total_model, total_bkg )
        weight = np.array( [ w/np.sum(w)  for r in radii ] )

                
        #restrict profiles to the user-specified analysis bins.
        area=area[:,good_bins]
        signal=signal[:,good_bins]
        model=model[:,good_bins]
        counts=counts[:,good_bins]
        bkg=bkg[:,good_bins]
        
        #average over the analysis bins
        
        excess_data =  np.average( signal/area , weights=weight, axis=1 )           
        excess_error = np.sqrt( np.sum( counts*weight*weight/(area*area) , axis=1 )) 
        excess_model = np.average( model/area , weights=weight, axis=1 )    
        
        return radii, excess_model, excess_data, excess_error, sorted(list_of_bin_names, key=int)


    def plot_radial_profile(self, ra, dec, bin_list = None, max_radius=3.0, n_radial_bins = 30, model_to_subtract = None ):

        """
        Plots radial profiles of data - background & model.
    
        :param ra: R.A. of origin for radial profile.
        :param dec: Declination of origin of radial profile.
        :param bin_list: List of analysis bins over which to average; if None, use HAWC default (bins 4-9).
        :param max_radius: Radius up to which the radial profile is evaluated; also used as the radius for the disk to calculate the gamma/hadron weights. Default: 3.0
        :param n_radial_bins: Number of bins for the radial profile. Default: 30.
        :param model_to_subtract: Another model that is to be subtracted from the data excess. Default: None.
        
        :return: plot of data - background vs model radial profiles.
        """

        radii, excess_model, excess_data, excess_error, list_of_bin_names = self.get_radial_profile( ra, dec, bin_list, max_radius, n_radial_bins, model_to_subtract )
        
        fig, ax = plt.subplots()
        
        plt.errorbar(radii, excess_data, yerr=excess_error, capsize=0,
                         color='black', label='Excess (data-bkg)', fmt='.')

        plt.plot(radii, excess_model, label='Model')

        plt.legend(bbox_to_anchor=(1.0, 1.0), loc="upper right",
                       numpoints=1)

        plt.axhline(0, linestyle='--')

        x_limits = [0, max_radius]
        plt.xlim = x_limits

        plt.ylabel("Apparent radial excess [sr$^{-1}$]")
        plt.xlabel("Distance from source at (%.2f$^{\circ}$, %.2f$^{\circ}$) [$^{\circ}$]" % ( ra, dec ) )
        plt.title("Radial profile, bin%s %s"  % ("s" if len(list_of_bin_names)>1 else "", list_of_bin_names ) )
  

        ax.grid(True)
        
        plt.tight_layout()

        return fig





    def write_model_map(self, fileName, poisson=False):

        # This is to make sure we have computed the sources (otherwise the following method WriteModelMap will fail
        self._fill_model_cache()

        self._theLikeHAWC.WriteModelMap(fileName, poisson)

    def write_residual_map(self, fileName):
 
        self._fill_model_cache()

        self._theLikeHAWC.WriteResidualMap(fileName)

