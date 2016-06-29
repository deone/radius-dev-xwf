import os
from datetime import timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing.settings")

import django
django.setup()

from django.contrib.auth.models import User
from django.utils import timezone

from billing.settings import PACKAGE_TYPES_HOURS_MAP
from accounts.models import AccessPoint, Radcheck
from accounts.helpers import md5_password
from packages.models import PackageSubscription, InstantVoucher

import radiusd

REPLY_CODES_MESSAGES = {
    'VPI': 'Voucher Password Incorrect',
    'UPI': 'User Password Incorrect',
    'UIN': 'User Inactive',
}

def print_info(info):
    radiusd.radlog(radiusd.L_INFO, info)

def trim_value(val):
    return val[1:-1]

def create_subscription(voucher, package):
    now = timezone.now()
    ps = PackageSubscription.objects.create(
            radcheck=voucher, package=package, start=now,
            stop=now + timedelta(hours=PACKAGE_TYPES_HOURS_MAP[package.package_type]))

    return ps

##############################

def create_mac(param):
    called_station_id = trim_value(param).split(':')[0]
    return called_station_id.replace('-', ':')

# For simplicity, make Package Subscription reference Radcheck instead of Subscriber for now.
# - Eventually, move all extra user info into Radcheck and rename it to Subscriber. Password check will then happen in Subscriber (md5).
# Check for package subscription. If subscription exists, skip next step.
# Fetch subscriber - query User with username. If not found, query Radcheck with username. If found in Radcheck, create PackageSubscription.
# For instant users, check password by md5-hashing password and comparing it with password in Radcheck.
# Skip account status check for instant users - log this to avoid confusion.
# To check AP eligibilty for instant users, return False in the 'else' block of AccessPoint.allows() if user is not an instance of User.

def get_or_create_subscription(voucher):
    try:
        subscription = PackageSubscription.objects.get(radcheck__username=voucher.username)
    except PackageSubscription.DoesNotExist:
        ivoucher = InstantVoucher.objects.get(radcheck__username=voucher.username)
        subscription = create_subscription(voucher, ivoucher.package)

    return subscription

def get_user_subscription(user):
    if user.subscriber.group is not None:
        try:
	    subscription = user.subscriber.group.grouppackagesubscription_set.all()[0]
	except:
	    return None
    else:
	try:
            subscription = user.radcheck.packagesubscription_set.all()[0]
	except:
	    return None

    return subscription

def get_user(username):
    try:
        user = User.objects.get(username__exact=username)
    except User.DoesNotExist:
        return None
    else:
        return user

def get_voucher(username):
    try:
        voucher_list = Radcheck.objects.filter(user=None).filter(username__exact=username)
        voucher = voucher_list[0]
    except IndexError:
        return None
    else:
        return voucher

def get_ap(ap_mac):
    try:
        ap = AccessPoint.objects.get(mac_address__exact=ap_mac)
    except AccessPoint.DoesNotExist:
        return None
    else:
        return ap

def check_voucher_password(voucher_password, user_password):
    print md5_password(user_password), voucher_password
    if md5_password(user_password) != voucher_password:
	return 'VPI'
    else:
        return True

def check_user_password(user, password):
    if not user.check_password(password):
        return 'UPI'
    else:
        return True 

def check_user_account_status(user):
    if user.is_active:
        return True
    else:
        return 'UIN'

def check_user_eligibility_on_ap(user, ap):
    if ap.allows(user):
        return True
    else:
        return False

def set_logged_in(user):
    try:
        subscriber = user.subscriber
    except:
        pass
    else:
        if subscriber.group is not None:
            user.radcheck.is_logged_in = True
            user.radcheck.save()

    return user

def check_subscription_validity(subscription, user):
    if subscription.is_valid():
        now = timezone.now()

        package_period = str((subscription.stop - now).total_seconds())
        package_period = package_period.split(".")[0]

        bandwidth_limit = str(float(subscription.package.speed) * 1000000)
        bandwidth_limit = bandwidth_limit.split('.')[0]

        set_logged_in(user)

        return (radiusd.RLM_MODULE_OK,
            (('Session-Timeout', package_period),('Maximum-Data-Rate-Upstream', bandwidth_limit),('Maximum-Data-Rate-Downstream', bandwidth_limit)),
            (('Auth-Type', 'python'),))
    else:
        return (radiusd.RLM_MODULE_REJECT,
            (('Reply-Message', 'Subscription Invalid'),), (('Auth-Type', 'python'),))

def display_reply_message(error_code):
    return (radiusd.RLM_MODULE_REJECT,
            (('Reply-Message', REPLY_CODES_MESSAGES[error_code]),), (('Auth-Type', 'python'),))