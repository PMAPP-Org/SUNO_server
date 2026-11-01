"""Ajustes de formato numérico sobre los de Django para `es`.

Django 5 usa el espacio duro (U+00A0) como separador de miles en `es`; en
Colombia se escribe con punto (1.234.567,89), como en el Excel de los usuarios.
Todo lo demás (coma decimal, grupos de 3) viene de Django.
"""

THOUSAND_SEPARATOR = "."
