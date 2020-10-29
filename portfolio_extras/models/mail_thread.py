
from odoo import models, api


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    @api.model_create_multi
    def create(self, vals_list):
        # remove auto subscription of the person creating the record
        self = self.with_context(mail_create_nosubscribe=True)
        return super(MailThread, self).create(vals_list)

    @api.multi
    def _message_auto_subscribe(self, updated_values):

        # Disable auto-subscription, except for a few identified cases
        if any(self.env.context.get(key) for key in ['mark_invoice_as_sent', 'mark_so_as_sent', 'mark_rfq_as_sent']):
            return super(MailThread, self)._message_auto_subscribe(updated_values)
        return True

