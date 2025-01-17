from labdevices.exceptions import CommunicationError_ProtocolViolation
from labdevices.exceptions import CommunicationError_Timeout
from labdevices.exceptions import CommunicationError_NotConnected

from labdevices.oscilloscope import Oscilloscope, OscilloscopeSweepMode, OscilloscopeTriggerMode, OscilloscopeTimebaseMode, OscilloscopeRunMode, OscilloscopeCouplingMode
from labdevices.scpi import SCPIDeviceEthernet
import atexit
import re

from time import sleep

import socket

import logging
import datetime
from enum import Enum, IntEnum

class OscilloscopeMeasurementType(Enum):
    VPP = 0
    RRPH = 1
    FFPH = 2
    VMIN = 3
    VMAX = 4
    VRMS = 5
    VAVG = 6
    OVER = 7
    FREQ = 8
    PER = 9
    
    @classmethod
    def has_value(cls, v):
        return v in cls._value2member_map_
    
class OscilloscopeBandwidthMode(Enum):
    OFF = 0
    BW20 = 1
    
    @classmethod
    def has_value(cls, v):
        return v in cls._value2member_map_

class PYDHO800(Oscilloscope):
    def __init__(
        self,

        address=None,
        port=5555,

        useNumpy = False,
        
        rawMode = False,     # Sets the number of samples to retrieve up to the current memory depth of the scope
        samplePoints = 1000, 
    ):
        self._scpi = SCPIDeviceEthernet(address, port, None)
        self._rawMode = rawMode
        self._samplePoints = samplePoints

        super().__init__(
            nChannels = 4,
            supportedSweepModes = [
                OscilloscopeSweepMode.AUTO,
                OscilloscopeSweepMode.NORMAL,
                OscilloscopeSweepMode.SINGLE
            ],
            supportedTriggerModes = [
                OscilloscopeTriggerMode.EDGE,
                OscilloscopeTriggerMode.PULSE,
                OscilloscopeTriggerMode.SLOPE
            ],
            supportedTimebaseModes = [
                OscilloscopeTimebaseMode.MAIN,
                OscilloscopeTimebaseMode.XY,
                OscilloscopeTimebaseMode.ROLL
            ],
            supportedRunModes = [
                OscilloscopeRunMode.STOP,
                OscilloscopeRunMode.RUN,
                OscilloscopeRunMode.SINGLE
            ],
            supportedChannelCouplingModes = [
                OscilloscopeCouplingMode.GND,
                OscilloscopeCouplingMode.AC,
                OscilloscopeCouplingMode.DC
            ],
            
            timebaseScale = (5e-9, 1000.0),
			voltageScale = (500e-6, 10),
            triggerForceSupported = True
        )

        self._probe_ratios = [ 1, 1, 1, 1 ]

        self._use_numpy = useNumpy

        atexit.register(self.__close)

    # Connection handling

    def _connect(self, address = None, port = None):
        if self._scpi.connect(address, port):
            # Ask for identity and verify ...
            idnString = self._idn()
            if not idnString.startswith("RIGOL TECHNOLOGIES,DHO"):
                self._disconnect()
                raise ValueError(f"Unsupported device, identifies as {idnString}")

            idnParts = idnString.split(",")
            self._id = {
                'manufacturer' : idnParts[0],
                'product'      : idnParts[1],
                'serial'       : idnParts[2],
                'version'      : idnParts[3]
            }
        return True

    def _disconnect(self):
        self._scpi.disconnect()

    def _isConnected(self):
        return self._scpi.isConnected()

    # Context management

    def __enter__(self):
        if self._usedConnect:
            raise ValueError("Cannot use context management (with) on a connected port")

        # Run our internal connect method ...
        self._connect()

        self._usesContext = True
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__close()
        self._usesContext = False
    def __close(self):
        atexit.unregister(self.__close)
        if self._scpi.isConnected():
            self._off()
            self._disconnect()

    # Commands

    def _idn(self):
        return self._scpi.scpiQuery("*IDN?")

    def _identify(self):
        resp = self._idn()
        if resp is None:
            return None

        idnParts = resp.split(",")

        return {
            'manufacturer' : idnParts[0],
            'product'      : idnParts[1],
            'serial'       : idnParts[2],
            'version'      : idnParts[3]
        }

    def _off(self):
        pass

    def _set_channel_enable(self, channel, enabled):
        if (channel < 0) or (channel > 3):
            raise ValueError("Invalid channel number for DHO800/900")
        if enabled:
            self._scpi.scpiCommand(f":CHAN{channel+1}:DISP ON")
        else:
            self._scpi.scpiCommand(f":CHAN{channel+1}:DISP OFF")

    def _is_channel_enabled(self, channel):
        if (channel < 0) or (channel > 3):
            raise ValueError("Invalid channel number for DHO800/900")

        resp = self._scpi.scpiQuery(f":CHAN{channel+1}:DISP?")
        try:
            resp = int(resp)
            if resp == 1:
                return True
            elif resp == 0:
                return False
        except:
            pass

        raise CommunicationError_ProtocolViolation("Failed to query enabled status of channel")

    def _set_sweep_mode(self, mode):
        if mode == OscilloscopeSweepMode.AUTO:
            self._scpi.scpiCommand(":TRIG:SWE AUTO")
        elif mode == OscilloscopeSweepMode.NORMAL:
            self._scpi.scpiCommand(":TRIG:SWE NORM")
        elif mode == OscilloscopeSweepMode.SINGLE:
            self._scpi.scpiCommand(":TRIG:SWE SING")
        else:
            raise ValueError(f"Unknown sweep mode {mode} passed")

    def _get_sweep_mode(self):
        resp = self._scpi.scpiCommand(f":TRIG:SWE?")

        modes = {
            "NORM" : OscilloscopeSweepMode.NORMAL,
            "SING" : OscilloscopeSweepMode.SINGLE,
            "AUTO" : OscilloscopeSweepMode.AUTO
        }
        if resp in modes:
            return modes[resp]
        else:
            raise CommunicationError_ProtocolViolation(f"Unknown sweep mode {resp} received from device")


    def _set_trigger_mode(self, mode):
        if mode == OscilloscopeTriggerMode.EDGE:
            self._scpi.scpiCommand(":TRIG:MODE EDGE")
        elif mode == OscilloscopeTriggerMode.PULSE:
            self._scpi.scpiCommand(":TRIG:MODE PULS")
        elif mode == OscilloscopeTriggerMode.SLOPE:
            self._scpi.scpiCommand(":TRIG:MODE SLOP")

    def _get_trigger_mode(self):
        resp = self._scpi.scpiQuery(f":TRIG:MODE?")

        modes = {
            "EDGE" : OscilloscopeTriggerMode.EDGE,
            "PULS" : OscilloscopeTriggerMode.PULSE,
            "SLOP" : OscilloscopeTriggerMode.SLOPE
        }
        if resp in modes:
            return modes[resp]
        else:
            raise CommunicationError_ProtocolViolation(f"Unknown trigger mode {resp} received from device")

    def _force_trigger(self):
        self._scpi.scpiCommand(":TFOR")

    def _set_run_mode(self, mode):
        if mode == OscilloscopeRunMode.STOP:
            self._scpi.scpiCommand(":STOP")
        elif mode == OscilloscopeRunMode.SINGLE:
            self._scpi.scpiCommand(":SING")
        elif mode == OscilloscopeRunMode.RUN:
            self._scpi.scpiCommand(":RUN")

    def _get_run_mode(self):
        resp = self._scpi.scpiQuery(":TRIG:STAT?")

        if resp == "STOP":
            return OscilloscopeRunMode.STOP
        elif (resp == "RUN") or (resp == "AUTO"):
            return OscilloscopeRunMode.RUN
        elif resp == "WAIT":
            return OscilloscopeRunMode.RUN
            
    def _set_timebase_mode(self, mode):
        modestr = {
            OscilloscopeTimebaseMode.MAIN : "MAIN",
            OscilloscopeTimebaseMode.XY   : "XY",
            OscilloscopeTimebaseMode.ROLL : "ROLL"
        }

        if mode not in modestr:
            raise ValueError(f"Unsupported timebase mode {mode}")

        self._scpi.scpiCommand(f":TIM:MODE {modestr[mode]}")

    def _get_timebase_mode(self):
        resp = self._scpi.scpiQuery(f":TIM:MODE?")

        modes = {
            "MAIN" : OscilloscopeTimebaseMode.MAIN,
            "XY"   : OscilloscopeTimebaseMode.XY,
            "ROLL" : OscilloscopeTimebaseMode.ROLL
        }
        if resp in modes:
            return modes[resp]
        else:
            raise CommunicationError_ProtocolViolation(f"Unknown timebase mode {resp} received from device")

    def _set_timebase_scale(self, scale):
        # The setable timebase scale depends on the model and the current
        # mode. Check if we are in range ...

        tbLimitsYT = {
            "DHO802"  : (5e-9, 1000),
            "DHO804"  : (5e-9, 1000),
            "DHO812"  : (5e-9, 1000),
            "DHO814"  : (5e-9, 1000),
            "DHO914"  : (5e-9, 1000),
            "DHO914S" : (5e-9, 1000),
            "DHO924"  : (5e-9, 1000),
            "DHO924S" : (5e-9, 1000),
        }

        if self._get_timebase_mode() == OscilloscopeTimebaseMode.ROLL:
            if (scale < 200e-3) or (scale > 1000.0):
                raise ValueError("Timebase values in roll mode have to be in range 200ms to 1ks")
        else:
            if self._id['product'] not in tbLimitsYT:
                raise ValueError("Failed to validate timebase region for unknown product {self._id['product']}")

            if (scale < tbLimitsYT[self._id['product']][0]) or (scale > tbLimitsYT[self._id['product']][1]):
                raise ValueError(f"Timebase scale {scale}s/div is out of range {tbLimitsYT[self._id['product']][0]}s/div to {tbLimitsYT[self._id['product']][1]}s/div for {self._id['product']}")

        # Set timebase
        self._scpi.scpiCommand(f":TIM:SCAL {scale}")

    def _get_timebase_scale(self):
        resp = self._scpi.scpiQuery(":TIM:SCAL?")
        try:
            resp = float(resp)
            return resp
        except:
            pass

        raise CommunicationError_ProtocolViolation(f"Unknown response for timebase scale: {resp}")

    def _set_channel_coupling(self, channel, couplingMode):
        if (channel < 0) or (channel > 3):
            raise ValueError(f"Supplied channel number {channel} is out of bounds")

        modestr = {
            OscilloscopeCouplingMode.DC  : "DC",
            OscilloscopeCouplingMode.AC  : "AC",
            OscilloscopeCouplingMode.GND : "GND"
        }

        if couplingMode not in modestr:
            raise ValueError(f"Unsupported coupling mode {couplingMode}")

        self._scpi.scpiCommand(f":CHAN{channel+1}:COUP {modestr[couplingMode]}")

    def _get_channel_coupling(self, channel):
        if (channel < 0) or (channel > 3):
            raise ValueError(f"Supplied channel number {channel} is out of bounds")

        resp = self._scpi.scpiQuery(f":CHAN{channel+1}:COUP?")

        modes = {
            "DC"   : OscilloscopeCouplingMode.DC,
            "AC"   : OscilloscopeCouplingMode.AC,
            "GND"  : OscilloscopeCouplingMode.GND
        }
        if resp in modes:
            return modes[resp]
        else:
            raise CommunicationError_ProtocolViolation(f"Unknown coupling mode {resp} received from device")

    def _set_channel_probe_ratio(self, channel, ratio):
        if (channel < 0) or (channel >= self._nchannels):
            raise ValueError(f"Channel index {channel} is out of bounds")
        if ratio not in [ 0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000 ]:
            raise ValueError(f"Ratio {ratio} is not supported by this device")

        self._probe_ratios[channel] = ratio
        self._scpi.scpiCommand(f":CHAN{channel+1}:PROB {ratio}")

    def _get_channel_probe_ratio(self, channel):
        if (channel < 0) or (channel > 3):
            raise ValueError(f"Supplied channel number {channel} is out of bounds")
        resp = self._scpi.scpiQuery(f":CHAN{channel+1}:PROB?")

        try:
            resp = float(resp)
        except:
            return None

        if resp not in [ 0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000 ]:
            raise CommunicationError_ProtocolViolation(f"Received unsupported probe ratio {resp}")
        return resp

    def _set_channel_scale(self, channel, scale):
        if (channel < 0) or (channel > 3):
            raise ValueError(f"Supplied channel number {channel} is out of bounds")
        scale = float(scale)

        # Check it's a scale that's actually setable - one has to also look at channel probe ratio though ...
        setableScales = [ 500e-6, 1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2, 1e-1, 2e-1, 5e-1, 1, 2, 5, 10 ]

        currentProbeRatio = self._get_channel_probe_ratio(channel)
        if currentProbeRatio is None:
            raise CommunicationError_ProtocolViolation("Failed to query current probe ratio")
        scale = scale / currentProbeRatio
        
        match_scale = 0
        for _scale in setableScales:
            if (float(_scale) < float(scale)):
                match_scale = _scale
                
        if match_scale not in setableScales:
            raise ValueError("Scale out of range [{500e-6 * currentProbeRatio};{10 * currentProbeRatio}] ({currentProbeRatio}x probe selected) in 1,2,5 steps")

        self._scpi.scpiCommand(f":CHAN{channel+1}:SCAL {match_scale}")

    def _get_channel_scale(self, channel):
        if (channel < 0) or (channel > 3):
            raise ValueError(f"Supplied channel number {channel} is out of bounds")
        resp = self._scpi.scpiQuery(f":CHAN{channel+1}:SCAL?")
        try:
            resp = float(resp)
        except:
            return None

        scalefactor = self._get_channel_probe_ratio(self, channel)
        if scalefactor is None:
            raise CommunicationError_ProtocolViolation("Failed to query current probe ratio")

        return resp * scalefactor

    def _waveform_get_xscale(self):
        xinc = self._scpi.scpiQuery(":WAV:XINC?")
        if xinc is None:
            raise CommunicationError_ProtocolViolation("Did not receive valid response to XINC")
        xorigin = self._scpi.scpiQuery(":WAV:XOR?")
        if xorigin is None:
            raise CommunicationError_ProtocolViolation("Did not receive valid response to XORIGIN")
        xref = self._scpi.scpiQuery(":WAV:XREF?")
        if xref is None:
            raise CommunicationError_ProtocolViolation("Did not receive valid response to XREF")

        try:
            xinc = float(xinc)
            xorigin = float(xorigin)
            xref = float(xref)
        except:
            raise CommunicationError_ProtocolViolation("Did not receive valid reply on XINC, XORIGIN or XREF")

        # This is:
        #	Interval between two neighboring points
        #	Start time of currently selected point after trigger
        #	Reference time (should be 0)
        return xinc, xorigin, xref

    def _waveform_get_yscale(self):
        xinc = self._scpi.scpiQuery(":WAV:YINC?")
        if xinc is None:
            raise CommunicationError_ProtocolViolation("Did not receive valid response to YINC")
        xorigin = self._scpi.scpiQuery(":WAV:YOR?")
        if xorigin is None:
            raise CommunicationError_ProtocolViolation("Did not receive valid response to YORIGIN")
        xref = self._scpi.scpiQuery(":WAV:YREF?")
        if xref is None:
            raise CommunicationError_ProtocolViolation("Did not receive valid response to YREF")

        try:
            xinc = float(xinc)
            xorigin = float(xorigin)
            xref = float(xref)
        except:
            raise CommunicationError_ProtocolViolation("Did not receive valid reply on YINC, YORIGIN or YREF")

        # This is:
        #	Interval between two neighboring points
        #	Start time of currently selected point after trigger
        #	Reference time (should be 0)
        return xinc, xorigin, xref
    
    def _get_num_points(self):
        resp = self._scpi.scpiQuery(":WAV:POIN?")
        return resp

    def _query_waveform(self, channel, stats = None):
        """ When raw mode is enabled, the number of points is set by the scope's memory depth setting
            This will generally take significantly longer as the memory depth is much larger """

        if isinstance(channel, list) or isinstance(channel, tuple):
            resp = None

            for ch in channel:
                resp_next = self._query_waveform(ch)
                if resp is None:
                    resp = {
                        'x' : resp_next['x'],
                        f"y{ch}" : resp_next['y']
                    }
                else:
                    resp[f"y{ch}"] = resp_next['y']
            return resp
        
        if (channel < 0) or (channel >= self._nchannels):
            raise ValueError(f"Channel {channel} is out of range [0;{self._nchannels-1}]")
        if self._rawMode:
            if self._get_run_mode() != OscilloscopeRunMode.STOP:
                raise CommunicationError_ProtocolViolation("You must run OscilloscopeRunMode.STOP before capturing in raw mode")
            self._scpi.scpiCommand(f":WAV:MODE RAW")
            self._scpi.scpiCommand(f":WAV:POIN RAW")
        else:
            self._scpi.scpiCommand(f":WAV:MODE NORM")
            self._scpi.scpiCommand(f":WAV:POIN {self._samplePoints} NORM")
        self._scpi.scpiCommand(f":WAV:SOUR CHAN{channel+1}")
        self._scpi.scpiCommand(f":WAV:FORM ASCII")
        resppre = self._scpi.scpiQuery(":WAV:PRE?")
        respdata = self._scpi.scpiQuery(":WAV:DATA?")

        if (resppre is None) or (respdata is None):
            raise CommunicationError_ProtocolViolation("Failed to query trace from DHO800")

        # Parse preamble ...
        pre = resppre.split(",")
        if len(pre) != 10:
            raise CommunicationError_ProtocolViolation("Unknown preamble format")

        if int(pre[0]) != 2:
            raise CommunicationError_ProtocolViolation(f"Requested ASCII but received format {pre[0]}")
        if (int(pre[1]) != 0) and (int(pre[1]) != 2):
            raise CommunicationError_ProtocolViolation(f"Requested Normal(0)/Raw(2) data, but received {pre[1]}")
        points = int(pre[2])
        avgcount = int(pre[3])
        xinc = float(pre[4])
        xorigin = float(pre[5])
        xref = float(pre[6])
        yinc = float(pre[7])
        yorigin = float(pre[8])
        yref = float(pre[9])

        wavedata = respdata.split(",")
        
        """ Only relevent for raw mode wheree more than 999_999 samples are requested
            Every 999_999 samples, there is a gltich where two samples are received on one line
            Incase Rigol fixes this, we make sure the length of the line is greater than one data point 
            EDIT: The interval is different on the DHO914S, so we only check for the length of the line being greater than one sample point """
        i = 0

        while(i < len(wavedata)):
            try:
                if(len(wavedata[i]) > 14):
                    two_fold = wavedata[i].split('e', 1)
                    first_num = two_fold[0] + 'e' + two_fold[1][:3]
                    second_num = two_fold[1][3:]
                    
                    wavedata.pop(i)
                    wavedata.insert(i, float(first_num))
                    wavedata.insert(i+1, float(second_num))
                    i += 2
                else:
                    data_float = float(wavedata[i])
                    wavedata[i] = data_float
                    i += 1
            except:
                if(i != len(wavedata) - 1):
                    raise CommunicationError_ProtocolViolation(f"An error occured trying to fix the glitch where two values are combined on one line from the scope \
                        Data received: {wavedata[i]}. \
                        First parsed number: {first_num}, Second parsed number: {second_num}")
            
        # Build x axis ...
        if self._use_numpy:
            import numpy as np
            dpoints = len(wavedata)
            xdata = np.linspace(xorigin, xorigin + dpoints * xinc, dpoints)
            ydata = np.asarray(wavedata)
        else:
            xdata = []
            curx = xorigin
            dpoints = len(wavedata)
            for i in range(dpoints):
                xdata.append(curx)
                curx = curx + xinc
            ydata = wavedata

        # Return trace X and Y axis ...
        #
        # The baseclass might add some statistics later on to the same dictionary

        res = {
            'x' : xdata,
            'y' : ydata
        }

        return res
    
    def get_memory_depth(self):
        resp = self._scpi.scpiQuery(":ACQ:MDEP?")
        return resp
    
    def get_channel_bandwidth(self, channel):
        if (channel < 0) or (channel > 3):
            raise ValueError("Invalid channel number for DHO800/900")
        resp = self._scpi.scpiQuery(f":CHAN{channel+1}:BWL?")
        return resp
    
    def set_channel_bandwidth(self, channel, bandwidth = 'OFF'):
        bw_modes = {
            "OFF"   : OscilloscopeBandwidthMode.OFF,
            "20M"   : OscilloscopeBandwidthMode.BW20
        }
        
        if (channel < 0) or (channel > 3):
            raise ValueError("Invalid channel number for DHO800/900")
            
        if bandwidth not in bw_modes:
            raise ValueError(f"Unsupported OscilloscopeBandwidthMode {bandwidth}")
            
        resp = self._scpi.scpiCommand(f":CHAN{channel+1}:BWL {bandwidth}")
        return resp
    
    def get_channel_measurement(self, type, channel = None, refchannel = None):
        typestr = {
            "VPP" : OscilloscopeMeasurementType.VPP ,
            "RRPH" : OscilloscopeMeasurementType.RRPH ,
            "FFPH" : OscilloscopeMeasurementType.FFPH ,
            "VMIN" : OscilloscopeMeasurementType.VMIN ,
            "VMAX" : OscilloscopeMeasurementType.VMAX ,
            "VRMS" : OscilloscopeMeasurementType.VRMS ,
            "VAVG" : OscilloscopeMeasurementType.VAVG ,
            "OVER" : OscilloscopeMeasurementType.OVER ,
            "FREQ" : OscilloscopeMeasurementType.FREQ ,
            "PER" : OscilloscopeMeasurementType.PER 
        }
        if (channel < 0) or (channel > 3):
            raise ValueError("Invalid channel number for DHO800/900")
            
        if type not in typestr:
            raise ValueError(f"Unsupported OscilloscopeMeasurementType {type}")
            
        if (channel is None):
            raise ValueError(f"Missing channel parameter in function call for channel {channel}")
            
        if ((typestr[type] is OscilloscopeMeasurementType.RRPH) or (typestr[type] is OscilloscopeMeasurementType.FFPH)):
            if (refchannel is None):
                raise ValueError(f"Missing refchannel parameter in function call for type {type}")
            else:
                if (refchannel < 0) or (refchannel > 3):
                    raise ValueError("Invalid refchannel number for DHO800/900")
                
            # out of range scope resonse is 9.9e+37
            resp1 = 0
            resp2 = 1
            while((resp1 >= 9.9e+37) or (abs(resp1) > (abs(resp2) * 1.1)) or  (abs(resp1) < (abs(resp2) * 0.9))):
                resp1 = float(self._scpi.scpiQuery(f":MEAS:ITEM? {type},CHAN{refchannel+1},CHAN{channel+1}"))
                resp2 = float(self._scpi.scpiQuery(f":MEAS:ITEM? {type},CHAN{refchannel+1},CHAN{channel+1}"))
            resp = (resp1 + resp2 ) /2
        else:
            resp = self._scpi.scpiQuery(f":MEAS:ITEM? {type},CHAN{channel+1}")
                        
        if (resp is None):
            raise CommunicationError_ProtocolViolation("Failed measurement from DHO800")
        return resp
    

    class memory_depth_t(Enum):
        """ These are in number of samples from the scope, not bytes """
        AUTO = "AUTO"
        M_1K = "1k"
        M_10K = "10k"
        M_100K = "100k"
        M_1M = "1M"
        M_10M = "10M"
        M_25M = "25M"
        M_50M = "50M"
        
    def set_memory_depth(self, depth: memory_depth_t):
        if not isinstance(depth, self.memory_depth_t):
            raise ValueError("Invalid memory depth specified")

        self._scpi.scpiCommand(f":ACQ:MDEP {depth.value}")
    

    # Signal Generator Settings (Only relevant for DHO914S and DHO924S)
        
    class signal_gen_waveform_t(Enum):
        SINE = "SIN",
        SQUARE = "SQU",
        RAMP = "RAMP",
        DC = "DC",
        NOISE = "NOIS",
        ARB = "ARB"

    def set_signal_gen_waveform(self, waveform: signal_gen_waveform_t):
        if not isinstance(waveform, self.signal_gen_waveform_t):
            raise ValueError("Invalid waveform specified")

        self._scpi.scpiCommand(f":SOUR:FUNC {waveform.value}")

    def get_signal_gen_waveform(self):
        resp = self._scpi.scpiQuery(":SOUR:FUNC?")
        return resp

    def set_signal_gen_freq(self, freq_Hz):
        self._scpi.scpiCommand(f":SOUR:FREQ {freq_Hz}")

    def get_signal_gen_freq(self):
        resp = self._scpi.scpiQuery(":SOUR:FREQ?")
        return resp
    
    def set_signal_gen_phase(self, phase_deg:float):
        """ Input is in degrees """
        self._scpi.scpiCommand(f":SOUR:PHAS {phase_deg}")

    def get_signal_gen_phase(self):
        resp = self._scpi.scpiQuery(":SOUR:PHAS?")
        return resp
    
    def set_signal_gen_amp(self, amp_Vpp: float):
        """ Input is in volts, max is 10Vpp"""
        print(f"Setting amp to {amp_Vpp}")
        self._scpi.scpiCommand(f":SOUR:VOLT:AMPL {amp_Vpp}")

    def get_signal_gen_amp(self):
        resp = self._scpi.scpiQuery(":SOUR:VOLT:AMPL?")
        return resp
    
    def set_signal_gen_offset(self, offset_V: float):
        """ Input is in volts"""
        self._scpi.scpiCommand(f":SOUR:VOLT:OFFS {offset_V}")

    def get_signal_gen_offset(self):
        resp = self._scpi.scpiQuery(":SOUR:VOLT:OFFS?")
        return resp
    