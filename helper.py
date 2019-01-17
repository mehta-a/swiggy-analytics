import os
import sys
import time
from collections import namedtuple
from exceptions import (SwiggyAPIError, SwiggyCliAuthError,
                        SwiggyCliConfigError, SwiggyCliQuitError,
                        SwiggyDBError)
from math import ceil

import requests
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.shortcuts import ProgressBar

from cli import get_input_value, quit_prompt
from constants import (PROGRESS_BAR_FORMATTER, PROGRESS_BAR_STYLE,
                       SWIGGY_API_CALL_INTERVAL, SWIGGY_LOGIN_URL,
                       SWIGGY_ORDER_URL, SWIGGY_URL)
from db import SwiggyDB
from utils import get_config, save_config

session = requests.Session()


def fetch_orders_info(orders):
    order_details = []
    order_items = []
    OrderDetails = namedtuple(
        'OrderDetails', ['order_id', 'order_total', 'restaurant_name', 'order_time', 'rain_mode', 'on_time'])
    OrderItems = namedtuple(
        'OrderItems', ['order_id', 'is_veg', 'name'])

    # filter orders which are delivered
    delivered_orders = list(filter(lambda i: i.get(
        'order_status', '') == 'Delivered', orders))
    for order in delivered_orders:
        order_id = order.get('order_id')
        order_total = order.get('order_total')
        restaurant_name = order.get('restaurant_name')
        order_time = order.get('order_time')
        rain_mode = order.get('rain_mode', False)
        on_time = order.get('on_time', True)

        order_details.append(OrderDetails(order_id=order_id,
                                          order_total=order_total,
                                          restaurant_name=restaurant_name,
                                          order_time=order_time,
                                          rain_mode=rain_mode,
                                          on_time=on_time))
        if order.get('order_items'):
            for item in order.get('order_items'):
                is_veg = item.get('is_veg')
                name = item.get('name')
                order_items.append(OrderItems(order_id=order_id,
                                              is_veg=is_veg,
                                              name=name))

    return {'order_details': order_details, 'order_items': order_items}


def fetch_orders(offset_id):
    response = session.get(SWIGGY_ORDER_URL + '?order_id=' + str(offset_id))
    return response.json().get('data').get('orders', [])


def initial_setup_prompt():
    """
    Prompt shown for the first time setup
    or when configure flag is passed.
    Fetch the keys from user and store in config file
    """
    try:
        swiggy_username = get_input_value(title='First time setup',
                                          text='Please enter your swiggy username. You can use your mobile number')
    except SwiggyCliQuitError:
        sys.exit("Bye")
    try:
        swiggy_password = get_input_value(
            title='First time setup',
            text='Please enter your swiggy password',
            password=True)
    except SwiggyCliQuitError:
        sys.exit("Bye")

    save_config(username=swiggy_username, password=swiggy_password)
    return None


def perform_login():
    establish_connection = session.get(SWIGGY_URL)
    # This is the most ugliest parsing I have ever written. Don't @ me
    csrf_token = establish_connection.text.split("csrfToken")[1].split("=")[
        1].split(";")[0][2:-1]

    if not csrf_token:
        raise SwiggyCliAuthError("Unable to parse CSRF Token. Login failed")

    # fetch username, password from config
    try:
        username, password = get_config()
    except SwiggyCliConfigError as e:
        raise e
    login_response = session.post(SWIGGY_LOGIN_URL, headers={'content-type': 'application/json'}, json={
                                  "mobile": username, "password": password, '_csrf': csrf_token})

    if login_response.text == "Invalid Request":
        perform_login()

    if login_response.status_code != 200:
        raise SwiggyCliAuthError(
            "Login response non success %s", login_response.status_code)


def get_orders(db):
    response = session.get(SWIGGY_ORDER_URL)
    if not response.json().get('data', None):
        raise SwiggyAPIError("Unable to fetch orders")

    # get the last order_id to use as offset param for next order fetch call
    orders = response.json().get('data').get('orders', None)
    if not orders:
        raise SwiggyAPIError("Unable to fetch orders")
    offset_id = orders[-1]['order_id']
    count = response.json().get('data')['total_orders']
    pages = ceil(count/10)

    label = "Fetching {} orders".format(count)

    with ProgressBar(style=PROGRESS_BAR_STYLE, formatters=PROGRESS_BAR_FORMATTER) as pb:
        for i in pb(range(pages), label=label):
            orders = fetch_orders(offset_id)
            if len(orders) == 0:
                break

            orders_info = fetch_orders_info(orders)
            try:
                db.insert_orders_details(orders_info.get('order_details'))
            except SwiggyDBError as e:
                print(e)
            try:
                db.insert_order_items(orders_info.get('order_items'))
            except SwiggyDBError as e:
                print(e)
            time.sleep(SWIGGY_API_CALL_INTERVAL)
            offset_id = orders[-1]['order_id']
