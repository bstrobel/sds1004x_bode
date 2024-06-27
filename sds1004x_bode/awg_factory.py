'''
Created on May 15, 2018

@author: 4x1md

Update of original file on Nov. 17 2018 by Dundarave to add entries needed for FY6600 support.
'''

from awgdrivers.dummy_awg import DummyAWG
from awgdrivers.jds6600 import JDS6600
from awgdrivers.bk4075 import BK4075
from awgdrivers.fy6600 import FY6600
from awgdrivers.ad9910 import AD9910
from awgdrivers.dg800 import RigolDG800
from awgdrivers.utg1000x import UTG1000x



class AwgFactory(object):

    def __init__(self):
        self.awgs = {}

    def add_awg(self, short_name, awg_class):
        self.awgs[short_name] = awg_class

    def get_class_by_name(self, short_name):
        return self.awgs[short_name]

    def get_names(self):
        out = []
        for a in self.awgs:
            out.append(a)
        return out


# Initialize factory
awg_factory = AwgFactory()
awg_factory.add_awg(DummyAWG.SHORT_NAME, DummyAWG)
awg_factory.add_awg(JDS6600.SHORT_NAME, JDS6600)
awg_factory.add_awg(BK4075.SHORT_NAME, BK4075)
awg_factory.add_awg(FY6600.SHORT_NAME, FY6600)
awg_factory.add_awg(AD9910.SHORT_NAME, AD9910)
awg_factory.add_awg(RigolDG800.SHORT_NAME, RigolDG800)
awg_factory.add_awg(UTG1000x.SHORT_NAME, UTG1000x)
