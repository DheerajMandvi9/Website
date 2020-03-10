import logging

from flask import (
    abort,
    current_app as app,
    request,
)
import pytransferwise
from pytransferwise.webhooks import verify_signature

from main import csrf
from models.payment import BankAccount
from . import payments

logger = logging.getLogger(__name__)


webhook_handlers = {}


def webhook(type=None):
    def inner(f):
        webhook_handlers[type] = f
        return f

    return inner


@csrf.exempt
@payments.route("/transferwise-webhook", methods=["POST"])
def transferwise_webhook():
    valid_signature = verify_signature(request.data, request.headers["X-Signature"],)
    if not valid_signature:
        logger.exception("Error verifying TransferWise webhook signature")
        abort(400)

    event_type = request.json.get("event_type")
    try:
        try:
            handler = webhook_handlers[event_type]
        except KeyError as e:
            handler = webhook_handlers[None]
        return handler(event_type, request.json)
    except Exception as e:
        logger.exception("Unhandled exception during TransferWise webhook")
        logger.info("Webhook data: %s", request.data)
        abort(500)


@webhook("balances#credit")
def transferwise_balance_credit(event_type, event):
    return ("", 204)


def transferwise_business_profile():
    client = pytransferwise.Client()
    profiles = client.profiles.list(type="business")
    return next(profiles, None)


def _collect_bank_accounts(borderless_account):
    for balance in borderless_account.balances:
        if not balance.bankDetails:
            continue

        address = ", ".join(
            [
                balance.bankAddress.addressFirstLine,
                balance.bankAddress.city,
                balance.bankAddress.postCode,
                balance.bankAddress.country,
            ]
        )
        yield BankAccount(
            sort_code=None,
            acct_id=None,
            currency=balance.bankDetails.currency,
            active=False,
            institution=balance.bankName,
            address=address,
            swift=balance.bankDetails.swift,
            iban=balance.bankDetails.iban,
            borderless_account_id=balance.id,
        )


def transferwise_retrieve_accounts():
    business_profile = transferwise_business_profile()
    if not business_profile:
        return

    client = pytransferwise.Client()
    borderless_accounts = client.borderless_accounts.list(
        profile_id=business_profile.id
    )
    for borderless_account in borderless_accounts:
        for bank_account in _collect_bank_accounts(borderless_account):
            yield bank_account


def transferwise_validate():
    """ Validate that TransferWise is configured and operational"""
    result = []

    env = app.config.get("TRANSFERWISE_ENVIRONMENT")
    if env == "sandbox":
        result.append((True, "Sandbox environment being used"))
    elif env == "live":
        result.append((True, "Live environment being used"))
    else:
        result.append((False, "No environment configured"))

    val = app.config.get("TRANSFERWISE_API_TOKEN", "")
    if len(val) == 36:
        result.append((True, "Access token set"))
    else:
        result.append((False, "Access token not set"))

    try:
        client = pytransferwise.Client()
        user = client.users.me()
        result.append((True, f"Connection to TransferWise API succeeded"))
    except Exception as e:
        result.append((False, f"Unable to connect to TransferWise: {e}"))

    business_profile = transferwise_business_profile()
    if business_profile:
        result.append((True, "TransferWise business profile exists"))
    else:
        result.append((False, "TransferWise business profile does not exist"))

    webhooks = client.subscriptions.list(profile_id=business_profile.id)
    if webhooks:
        result.append((True, "Webhook event subscriptions are present"))
    else:
        result.append((False, "Webhook event subscriptions are not present"))

    return result
