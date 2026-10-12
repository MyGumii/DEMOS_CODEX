"""Internal collaborative Notion contracts.

The package is inert: importing it does not inspect environment variables,
open data roots, register routes, or start services.
"""

from .pair_config import PairConfiguration, PairConfigurationLoad, PairHost, load_pair_configuration

__all__ = [
    "PairConfiguration",
    "PairConfigurationLoad",
    "PairHost",
    "load_pair_configuration",
]

