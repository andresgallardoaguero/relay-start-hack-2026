# Script: session.py
# Purpose: Look up how usual the device, the hour and the country of one purchase are for the card, and how fast purchase attempts follow each other
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo









#### Step 1: Define the session facts ####

# Name the time zone the customers live in. The hour counts of the card history are built in the same zone,
# so the hour of a purchase and the hours of the history always mean the same thing, in summer and in winter.
LOCAL_TIME_ZONE = ZoneInfo("Europe/Zurich")



# Name the channel of a payment that runs by itself on a schedule, which says nothing about when the customer shops
RECURRING_CHANNEL = "recurring"



# Hold what the history of the card says about the session of one purchase, as plain counts. No identifier is kept, so no guard can decide on one.
# Every count covers the approved purchases of the card outside the recurring channel.
# history_purchase_count is the number of purchases behind the hour counts. It is None for a card the history does not hold.
# device_purchase_count counts the purchases of the card from the device of the purchase. It is None for an unknown card, and 0 for a known card
# that never used the device. It is also None when the purchase states no device, because a device that is not stated is a missing fact and no new device.
# local_hour is the hour of the purchase in Swiss local time, from 0 to 23, and hour_purchase_count counts the purchases of the card in that hour.
# local_time_text is the same moment as the customer reads it, with hour and minute, as in "04.14".
# shop_country is the country of the shop, and country_purchase_count counts the purchases of the card in that country.
# recent_attempt_count is the number of purchase attempts in the ten minutes before the purchase, as the purchase message states it.
# is_recurring_channel is True for a payment that runs by itself on a schedule.
@dataclass(frozen = True)
class SessionFacts:
    card_is_known: bool
    history_purchase_count: Optional[int]
    device_purchase_count: Optional[int]
    local_hour: int
    local_time_text: str
    hour_purchase_count: Optional[int]
    shop_country: str
    country_purchase_count: Optional[int]
    recent_attempt_count: int
    is_recurring_channel: bool









#### Step 2: Read the hour and the device ####

# Convert the moment of a purchase to Swiss local time, so 02.14 in UTC is 04.14 in summer and 03.14 in winter
def convert_to_swiss_local_time(purchase_moment):
    return purchase_moment.astimezone(LOCAL_TIME_ZONE)



# Write a local moment with hour and minute as the customer reads it, as in "04.14"
def describe_local_time(local_moment):
    return local_moment.strftime("%H.%M")



# Count the purchases of a card from one device, where a device that is not stated gives None and a device the card never used gives 0
def count_purchases_from_device(card, device_text):
    if device_text.strip() == "":
        return None
    return card.device_purchase_counts.get(device_text, 0)









#### Step 3: Build the session facts of one purchase ####

# Build the facts for one purchase message. The card, the device, the shop country, the moment, the recent attempts and the channel
# are taken from the purchase, and the counts from the history of the card.
def build_session_facts(event, baselines):
    authorization = event.authorization
    local_moment = convert_to_swiss_local_time(authorization.timestamp)
    local_hour = local_moment.hour
    local_time_text = describe_local_time(local_moment)
    shop_country = authorization.merchant.merchant_country
    is_recurring_channel = authorization.channel == RECURRING_CHANNEL



    # Answer for an unknown card without any count, because nothing is known about what is usual for it
    card = baselines.get_card(authorization.card_id)
    if card is None:
        return SessionFacts(
            card_is_known = False,
            history_purchase_count = None,
            device_purchase_count = None,
            local_hour = local_hour,
            local_time_text = local_time_text,
            hour_purchase_count = None,
            shop_country = shop_country,
            country_purchase_count = None,
            recent_attempt_count = authorization.recent_attempt_count_10m,
            is_recurring_channel = is_recurring_channel,
        )



    # Look the device, the hour and the country up in the counts of the card, where a country the card never bought in gives 0
    return SessionFacts(
        card_is_known = True,
        history_purchase_count = sum(card.local_hour_purchase_counts),
        device_purchase_count = count_purchases_from_device(card, authorization.customer_device_id),
        local_hour = local_hour,
        local_time_text = local_time_text,
        hour_purchase_count = card.local_hour_purchase_counts[local_hour],
        shop_country = shop_country,
        country_purchase_count = card.country_purchase_counts.get(shop_country, 0),
        recent_attempt_count = authorization.recent_attempt_count_10m,
        is_recurring_channel = is_recurring_channel,
    )
