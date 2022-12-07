# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import fields, api, models, _
from odoo.tools.float_utils import float_compare
from odoo.tools import DEFAULT_SERVER_TIME_FORMAT, float_repr, float_round
from odoo.tools import html2plaintext
from .carvajal_request import CarvajalRequest

import pytz
import base64
import re

from collections import defaultdict
from datetime import timedelta
from functools import lru_cache


class AccountEdiFormat(models.Model):
    _inherit = 'account.edi.format'



    def _l10n_co_edi_generate_xml(self, invoice):
        '''Renders the XML that will be sent to Carvajal.'''

        def format_monetary(number, currency):
            # Format the monetary values to avoid trailing decimals (e.g. 90.85000000000001).
            return float_repr(number, currency.decimal_places)

        def get_notas():
            '''This generates notes in a particular format. These notes are pieces
            of text that are added to the PDF in various places. |'s are
            interpreted as newlines by Carvajal. Each note is added to the
            XML as follows:

            <NOT><NOT_1>text</NOT_1></NOT>

            One might wonder why Carvajal uses this arbitrary format
            instead of some extra simple XML tags but such questions are best
            left to philosophers, not dumb developers like myself.
            '''
            # Volume has to be reported in l (not e.g. ml).
            lines = invoice.invoice_line_ids.filtered(lambda line: line.product_uom_id.category_id == self.env.ref('uom.product_uom_categ_vol'))
            liters = sum(line.product_uom_id._compute_quantity(line.quantity, self.env.ref('uom.product_uom_litre')) for line in lines)
            total_volume = int(liters)

            # Weight has to be reported in kg (not e.g. g).
            lines = invoice.invoice_line_ids.filtered(lambda line: line.product_uom_id.category_id == self.env.ref('uom.product_uom_categ_kgm'))
            kg = sum(line.product_uom_id._compute_quantity(line.quantity, self.env.ref('uom.product_uom_kgm')) for line in lines)
            total_weight = int(kg)

            # Units have to be reported as units (not e.g. boxes of 12).
            lines = invoice.invoice_line_ids.filtered(lambda line: line.product_uom_id.category_id == self.env.ref('uom.product_uom_categ_unit'))
            units = sum(line.product_uom_id._compute_quantity(line.quantity, self.env.ref('uom.product_uom_unit')) for line in lines)
            total_units = int(units)

            withholding_amount = invoice.amount_untaxed + sum(invoice.line_ids.filtered(lambda line: line.tax_line_id and not line.tax_line_id.l10n_co_edi_type.retention).mapped('price_total'))
            amount_in_words = invoice.currency_id.with_context(lang=invoice.partner_id.lang or 'es_ES').amount_to_text(withholding_amount)
            shipping_partner = self.env['res.partner'].browse(invoice._get_invoice_delivery_partner_id())

            reg_a_tag = re.compile('<a.*?>')
            clean_narration = re.sub(reg_a_tag, '', invoice.narration) if invoice.narration else False
            narration = (html2plaintext(clean_narration or '') and html2plaintext(clean_narration) + ' ') + (invoice.invoice_origin or '')
            notas = [
                '1.-%s|%s|%s|%s|%s|%s' % (invoice.company_id.l10n_co_edi_header_gran_contribuyente or '',
                                          invoice.company_id.l10n_co_edi_header_tipo_de_regimen or '',
                                          invoice.company_id.l10n_co_edi_header_retenedores_de_iva or '',
                                          invoice.company_id.l10n_co_edi_header_autorretenedores or '',
                                          invoice.company_id.l10n_co_edi_header_resolucion_aplicable or '',
                                          invoice.company_id.l10n_co_edi_header_actividad_economica or ''),
                '2.-%s' % (invoice.company_id.l10n_co_edi_header_bank_information or '').replace('\n', '|'),
                ('3.- %s' % (narration or 'N/A'))[:500],
                '6.- %s|%s' % (html2plaintext(invoice.invoice_payment_term_id.note), amount_in_words),
                '7.- %s' % (invoice.company_id.website),
                '8.-%s|%s|%s' % (invoice.partner_id.commercial_partner_id._get_vat_without_verification_code() or '', shipping_partner.phone or '', invoice.invoice_origin and invoice.invoice_origin.split(',')[0] or ''),
                '10.- | | | |%s' % (invoice.invoice_origin and invoice.invoice_origin.split(',')[0] or 'N/A'),
                '11.- |%s| |%s|%s' % (total_units, total_weight, total_volume)
            ]

            return notas

        invoice = invoice.with_context(lang=invoice.partner_id.lang)
        code_to_filter = ['07', 'ZZ'] if invoice.move_type in ('in_invoice', 'in_refund') else ['ZZ']
        move_lines_with_tax_type = invoice.line_ids.filtered(lambda l: l.tax_line_id.l10n_co_edi_type.code not in [False] + code_to_filter)

        ovt_tax_codes = ('01C', '02C', '03C')
        ovt_taxes = move_lines_with_tax_type.filtered(lambda move: move.tax_line_id.l10n_co_edi_type.code in ovt_tax_codes).tax_line_id

        invoice_type_to_ref_1 = {
            'out_invoice': 'IV',
            'out_refund': 'NC',
        }

        def group_tax_retention(tax_values):
            return {'tax': tax_values['tax_id'], 'l10n_co_edi_type': tax_values['tax_id'].l10n_co_edi_type}

        def l10n_co_filter_to_apply(tax_values):
            return tax_values['tax_id'].l10n_co_edi_type.code not in code_to_filter

        tax_details = invoice._prepare_edi_tax_details(filter_to_apply=l10n_co_filter_to_apply, grouping_key_generator=group_tax_retention)
        retention_taxes = [(group, detail) for group, detail in tax_details['tax_details'].items() if detail['l10n_co_edi_type'].retention]
        regular_taxes = [(group, detail) for group, detail in tax_details['tax_details'].items() if not detail['l10n_co_edi_type'].retention]

        exempt_tax_dict = {}
        tax_group_covered_goods = self.env.ref('l10n_co.tax_group_covered_goods', raise_if_not_found=False)
        for line in invoice.invoice_line_ids:
            if tax_group_covered_goods and tax_group_covered_goods in line.mapped('tax_ids.tax_group_id'):
                exempt_tax_dict[line.id] = True

        retention_lines = move_lines_with_tax_type.filtered(
            lambda move: move.tax_line_id.l10n_co_edi_type.retention)
        retention_lines_listdict = defaultdict(list)
        for line in retention_lines:
            retention_lines_listdict[line.tax_line_id.l10n_co_edi_type.code].append(line)

        regular_lines = move_lines_with_tax_type - retention_lines
        regular_lines_listdict = defaultdict(list)
        for line in regular_lines:
            regular_lines_listdict[line.tax_line_id.l10n_co_edi_type.code].append(line)

        zero_tax_details = defaultdict(float)
        for line, tax_detail in tax_details['invoice_line_tax_details'].items():
            for tax, detail in tax_detail.get('tax_details').items():
                if not detail.get('tax_amount'):
                    for grouped_tax in detail.get('group_tax_details'):
                        tax = grouped_tax.get('tax_id')
                        zero_tax_details[tax.l10n_co_edi_type.code] += abs(grouped_tax.get('base_amount'))
        retention_taxes_new = self._l10n_co_edi_prepare_tim_sections(retention_lines_listdict, invoice.currency_id, True)
        regular_taxes_new = self._l10n_co_edi_prepare_tim_sections(regular_lines_listdict, invoice.currency_id, False, zero_tax_details)
        # The rate should indicate how many pesos is one foreign currency
        currency_rate = "%.2f" % (tax_details['base_amount'] / tax_details['base_amount_currency'])

        withholding_amount = '%.2f' % (invoice.amount_untaxed + sum(invoice.line_ids.filtered(lambda move: move.tax_line_id and not move.tax_line_id.l10n_co_edi_type.retention).mapped('price_total')))

        # edi_type
        if invoice.move_type == 'out_refund':
            edi_type = "91"
        elif invoice.move_type == 'out_invoice' and invoice.l10n_co_edi_debit_note:
            edi_type = "92"
        else:
            edi_type = "{0:0=2d}".format(int(invoice.l10n_co_edi_type))

        # validation_time
        validation_time = fields.Datetime.now()
        validation_time = pytz.utc.localize(validation_time)
        bogota_tz = pytz.timezone('America/Bogota')
        validation_time = validation_time.astimezone(bogota_tz)
        validation_time = validation_time.strftime(DEFAULT_SERVER_TIME_FORMAT) + "-05:00"

        # description
        description_field = None
        if invoice.move_type in ('out_refund', 'in_refund'):
            description_field = 'l10n_co_edi_description_code_credit'
        if invoice.move_type in ('out_invoice', 'in_invoice') and invoice.l10n_co_edi_debit_note:
            description_field = 'l10n_co_edi_description_code_debit'
        description_code = invoice[description_field] if description_field else None
        description = dict(invoice._fields[description_field].selection).get(description_code) if description_code else None

        xml_content = self._l10n_co_edi_get_electronic_invoice_template(invoice)._render({
            'invoice': invoice,
            'edi_type': edi_type,
            'company_partner': invoice.company_id.partner_id,
            'sales_partner': invoice.user_id,
            'invoice_partner': invoice.partner_id.commercial_partner_id,
            'retention_taxes': retention_taxes,
            'retention_taxes_new': retention_taxes_new,
            'regular_taxes': regular_taxes,
            'regular_taxes_new': regular_taxes_new,
            'tax_details': tax_details,
            'tax_types': invoice.mapped('line_ids.tax_ids.l10n_co_edi_type'),
            'exempt_tax_dict': exempt_tax_dict,
            'currency_rate': currency_rate,
            'shipping_partner': self.env['res.partner'].browse(invoice._get_invoice_delivery_partner_id()),
            'invoice_type_to_ref_1': invoice_type_to_ref_1,
            'ovt_taxes': ovt_taxes,
            'float_compare': float_compare,
            'notas': get_notas(),
            'withholding_amount': withholding_amount,
            'validation_time': validation_time,
            'delivery_date': invoice.invoice_date + timedelta(1),
            'description_code': description_code,
            'description': description,
            'format_monetary': format_monetary,
            '_l10n_co_edi_get_round_amount': self._l10n_co_edi_get_round_amount
        })
        return b'<?xml version="1.0" encoding="utf-8"?>' + xml_content.encode()

    def _l10n_co_edi_get_electronic_invoice_template(self, invoice):
        if invoice.move_type in ('in_invoice', 'in_refund'):
            return self.env.ref('l10n_co_edi.electronic_invoice_vendor_document_xml')
        return self.env.ref('l10n_co_edi.electronic_invoice_xml')

    def _l10n_co_post_invoice_step_1(self, invoice):
        '''Sends the xml to carvajal.
        '''
        # == Generate XML ==
        xml_filename = self._l10n_co_edi_generate_electronic_invoice_filename(invoice)
        xml = self._l10n_co_edi_generate_xml(invoice)
        attachment = self.env['ir.attachment'].create({
            'name': xml_filename,
            'res_id': invoice.id,
            'res_model': invoice._name,
            'type': 'binary',
            'raw': xml,
            'mimetype': 'application/xml',
            'description': _('Colombian invoice UBL generated for the %s document.', invoice.name),
        })

        # == Upload ==
        request = CarvajalRequest(invoice.move_type, invoice.company_id)
        response = request.upload(xml_filename, xml)

        if 'error' not in response:
            invoice.l10n_co_edi_transaction = response['transactionId']

            # == Chatter ==
            invoice.with_context(no_new_invoice=True).message_post(
                body=_('Electronic invoice submission succeeded. Message from Carvajal:<br/>%s', response['message']),
                attachment_ids=attachment.ids,
            )
            # Do not return the attachment because it is not signed yet.
        else:
            # Return the attachment with the error to allow debugging.
            response['attachment'] = attachment

        return response

    def _l10n_co_post_invoice_step_2(self, invoice):
        '''Checks the current status of an uploaded XML with Carvajal. It
        posts the results in the invoice chatter and also attempts to
        download a ZIP containing the official XML and PDF if the
        invoice is reported as fully validated.
        '''
        request = CarvajalRequest(invoice.move_type, invoice.company_id)
        response = request.check_status(invoice)
        if not response.get('error'):
            response['success'] = True
            invoice.l10n_co_edi_cufe_cude_ref = response['l10n_co_edi_cufe_cude_ref']

            # == Create the attachment ==
            if 'filename' in response and 'xml_file' in response:
                response['attachment'] = self.env['ir.attachment'].create({
                    'name': response['filename'],
                    'res_id': invoice.id,
                    'res_model': invoice._name,
                    'type': 'binary',
                    'datas': base64.b64encode(response['xml_file']),
                    'mimetype': 'application/xml',
                    'description': _('Colombian invoice UBL generated for the %s document.', invoice.name),
                })

            # == Chatter ==
            invoice.with_context(no_new_invoice=True).message_post(body=response['message'], attachments=response['attachments'])
        elif response.get('blocking_level') == 'error':
            invoice.l10n_co_edi_transaction = False

        return response

    # -------------------------------------------------------------------------
    # BUSINESS FLOW: EDI
    # -------------------------------------------------------------------------

    def _needs_web_services(self):
        # OVERRIDE
        return self.code == 'ubl_carvajal' or super()._needs_web_services()

    def _is_compatible_with_journal(self, journal):
        # OVERRIDE
        self.ensure_one()
        if self.code != 'ubl_carvajal':
            return super()._is_compatible_with_journal(journal)
        return journal.type == 'sale' or (journal.type == 'purchase' and journal.l10n_co_edi_is_support_document) and journal.country_code == 'CO'

    def _is_required_for_invoice(self, invoice):
        # OVERRIDE
        self.ensure_one()
        if self.code != 'ubl_carvajal':
            return super()._is_required_for_invoice(invoice)

        # Determine on which invoices the EDI must be generated.
        if invoice.move_type in ('in_invoice', 'in_refund') and invoice.country_code == 'CO':
            return bool(self.env.ref('l10n_co_edi.electronic_invoice_vendor_document_xml', False))
        return invoice.move_type in ('out_invoice', 'out_refund') and invoice.country_code == 'CO'

    def _check_move_configuration(self, move):
        # OVERRIDE
        self.ensure_one()
        edi_result = super()._check_move_configuration(move)
        if self.code != 'ubl_carvajal':
            return edi_result

        company = move.company_id
        journal = move.journal_id
        now = fields.Datetime.now()
        oldest_date = now - timedelta(days=5)
        newest_date = now + timedelta(days=10)
        if not company.l10n_co_edi_username or not company.l10n_co_edi_password or not company.l10n_co_edi_company or \
           not company.l10n_co_edi_account:
            edi_result.append(_("Carvajal credentials are not set on the company, please go to Accounting Settings and set the credentials."))
        if (move.move_type != 'out_refund' and not move.debit_origin_id) and \
           (not journal.l10n_co_edi_dian_authorization_number or not journal.l10n_co_edi_dian_authorization_date or not journal.l10n_co_edi_dian_authorization_end_date):
            edi_result.append(_("'Resolución DIAN' fields must be set on the journal %s", journal.display_name))
        if not move.partner_id.vat:
            edi_result.append(_("You can not validate an invoice that has a partner without VAT number."))
        if not move.company_id.partner_id.l10n_co_edi_obligation_type_ids:
            edi_result.append(_("'Obligaciones y Responsabilidades' on the Customer Fiscal Data section needs to be set for the partner %s.", move.company_id.partner_id.display_name))
        if not move.partner_id.commercial_partner_id.l10n_co_edi_obligation_type_ids:
            edi_result.append(_("'Obligaciones y Responsabilidades' on the Customer Fiscal Data section needs to be set for the partner %s.", move.partner_id.commercial_partner_id.display_name))
        if (move.l10n_co_edi_type == '2' and \
                any(l.product_id and not l.product_id.l10n_co_edi_customs_code for l in move.invoice_line_ids)):
            edi_result.append(_("Every exportation product must have a customs code."))
        elif move.invoice_date and not (oldest_date <= fields.Datetime.to_datetime(move.invoice_date) <= newest_date):
            move.message_post(body=_('The issue date can not be older than 5 days or more than 5 days in the future'))
        elif any(l.product_id and not l.product_id.default_code and \
                 not l.product_id.barcode and not l.product_id.unspsc_code_id for l in move.invoice_line_ids):
            edi_result.append(_("Every product on a line should at least have a product code (barcode, internal, UNSPSC) set."))

        if not move.company_id.partner_id.l10n_latam_identification_type_id.l10n_co_document_code:
            edi_result.append(_("The Identification Number Type on the company\'s partner should be 'NIT'."))
        if not move.partner_id.commercial_partner_id.l10n_latam_identification_type_id.l10n_co_document_code:
            edi_result.append(_("The Identification Number Type on the customer\'s partner should be 'NIT'."))

        return edi_result

    def _post_invoice_edi(self, invoices):
        # OVERRIDE
        self.ensure_one()
        if self.code != 'ubl_carvajal':
            return super()._post_invoice_edi(invoices)

        invoice = invoices  # No batching ensures that only one invoice is given as parameter
        if not invoice.l10n_co_edi_transaction:
            return {invoice: self._l10n_co_post_invoice_step_1(invoice)}
        else:
            return {invoice: self._l10n_co_post_invoice_step_2(invoice)}

    def _cancel_invoice_edi(self, invoices):
        # OVERRIDE
        self.ensure_one()
        return {invoice: {'success': True} for invoice in invoices}  # By default, cancel succeeds doing nothing.
