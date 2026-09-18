from .aldi import Aldi
from .aligro import Aligro
from .coop import Coop
from .lidl import Lidl
from .migros import Migros

# Order matters: it fixes each store's colour slot on the website.
STORES = {cls.key: cls for cls in (Aldi, Migros, Coop, Lidl, Aligro)}

__all__ = ["STORES", "Migros", "Coop", "Aldi", "Lidl", "Aligro"]
