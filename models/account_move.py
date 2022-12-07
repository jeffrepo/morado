from odoo import api, fields, models, tools, _
from odoo.exceptions import RedirectWarning, UserError, ValidationError, AccessError
from odoo.tools import float_compare, date_utils, email_split, email_re, html_escape, is_html_empty
from odoo.tools.misc import formatLang, format_date, get_lang

from datetime import date, timedelta
from collections import defaultdict
from contextlib import contextmanager
from itertools import zip_longest
from hashlib import sha256
from json import dumps

import ast
import json
import re
import warnings
import logging

#class AccountMoveLine(models.Model):
#    _inherit = 'account.move.line'
#
#    base_before_global_discounts = fields.Float('base_before_global_discounts')
    
class AccountMove(models.Model):
    _inherit = 'account.move'

    dsc1 = fields.Char('DSC_1')
    dsc2 = fields.Float('DSC_2')
    dsc3 = fields.Float('DSC_3')
    dsc4 = fields.Char('DSC_4')
    dsc5 = fields.Char('DSC_5')
    dsc6 = fields.Char('DSC_6')
    dsc7 = fields.Float('DSC_7')
    dsc8 = fields.Char('DSC_8')
    dsc9 = fields.Char('DSC_9')
    dsc10 = fields.Integer('DSC_10')

