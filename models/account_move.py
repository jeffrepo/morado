from odoo import api, fields, models, tools, _

class AccountMove(models.Model):
    _inherit = 'account.move'

    dsc1 = fields.Char('DSC_1')
    dsc2 = fields.Char('DSC_2')
    dsc3 = fields.Char('DSC_3')
    dsc4 = fields.Char('DSC_4')
    dsc5 = fields.Char('DSC_5')
    dsc6 = fields.Char('DSC_6')
    dsc7 = fields.Char('DSC_7')
    dsc8 = fields.Char('DSC_8')
    dsc9 = fields.Char('DSC_9')
    dsc10 = fields.Char('DSC_10')
