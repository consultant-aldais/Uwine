from odoo import api, fields, models
from odoo.tools.float_utils import float_compare


class AccountInvoice(models.Model):
    _inherit = 'account.invoice'

    @api.multi
    def _get_aml_for_register_payment(self):
        self.ensure_one()
        # The 419xxx accounts have been set as 'receivable' for some reason,
        # but they must not be involved in payment reconciliations: explicitely remove them
        return self.move_id.line_ids.filtered(lambda r: not r.reconciled and
                                                        r.account_id.internal_type in ('payable', 'receivable') and
                                                        not r.account_id.code.startswith('419'))

    @api.multi
    def name_get(self):
        result = []
        for inv in self:
            if inv.x_studio_tw_numero_facture:
                result.append((inv.id, inv.x_studio_tw_numero_facture))
            else:
                result.extend(super(AccountInvoice, inv).name_get())
        return result
