"""Data layer: real market data with provenance.

Every dataset carries a :class:`~ohcamel_quant.data.base.Provenance` record
(source, fetch time, ``synthetic: false``). Analytics modules never import
from here -- they take pandas objects -- so the maths is testable on
committed real fixtures without a network.
"""
