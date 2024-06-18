import os
from wyze_sdk import Client
# from wyze_sdk.models.devices import Device
# from wyze_sdk.errors import WyzeApiError

from argparse import ArgumentParser

import logging
import sys

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

def add_env(parser: ArgumentParser, key: str) -> None:
    val = os.environ.get(key)
    parser.add_argument(
        '--' + key.lower().replace('_', '-'), 
        required=val is None,
        default=val,
        metavar="'"+key.lower().replace('_', ' ').capitalize()+"'",
        help=f"Only required if the environment variable {key} is not set."
        )

def get_args():
    parser = ArgumentParser(description='Bulk manage Wyze cameras. Exit code is the number of failed operations (0 = success)')
    parser.add_argument(
        '--action', 
        choices=[
            'power_on', 'power_off', 
            'motion_alarm_on', 'motion_alarm_off',
            'floodlight_on', 'floodlight_off',
            'restart', 'list'
            ],
        required=True,
        help='Type of action to perform.'
        )
    parser.add_argument(
        '--log-level',
        choices=['CRITICAL','FATAL','ERROR','WARNING','INFO','DEBUG']
        )
    parser.add_argument(
        '--include',
        metavar='name1,name2',
        default="",
        help='Optional comma delimited list of cameras names of macs to include. If not specified, action is performed on all cameras unless excluded.'
    )
    parser.add_argument(
        '--exclude',
        metavar="name1,name2",
        default="",
        help='Optional comma delimited list of cameras names or macs to exclude.'
    )
    add_env(parser, 'WYZE_EMAIL')
    add_env(parser, 'WYZE_PASSWORD')
    add_env(parser, 'WYZE_KEY_ID')
    add_env(parser, 'WYZE_API_KEY')
    return parser.parse_args()

def perform_action(client: Client, include: list[str], exclude: list[str], action: str) -> int:
    fail = 0
    success = 0
    logging.info(f"Include: {include}; exclude: {exclude}")
    for camera in client.cameras.list():
        if include and not camera.nickname in include and not camera.mac in include:
            logging.debug(f"{camera.nickname} with mac {camera.mac} not in include list.")
            continue
        if exclude and (camera.nickname in exclude or camera.mac in exclude):
            logging.debug(f"{camera.nickname} with mac {camera.mac} in exclude list.")
            continue
        if action == "list":
            logging.info(f"Found device: {camera.nickname} with mac {camera.mac}")
            continue
        try:
            response = client._api_client().run_action(mac=camera.mac, provider_key=camera.product.model, action_key=action)
            if response.status_code != 200:
                logging.error(f"Could not perform {action} on {camera.nickname} with mac {camera.mac}. Code {response.status_code}. Content: {response.data}")
                fail += 1
            else:
                logging.info(f"Successfully performed {action} on {camera.nickname}")
                success += 1
        except Exception as e:
            logging.error(f"Could not perform {action} on {camera.nickname} with mac {camera.mac}: {e}.")
            fail += 1
    if success:
        logging.info(f"Successfully performed {success} {action} operations(s).")
    if fail:
        logging.warning(f"Failed to perform {fail} {action} operation(s).")
    return fail

def to_list(s: str) -> list[str]:
    return [item for item in s.split(',') if item.strip()]

try:
    args = get_args()

    if args.log_level:
        logging.getLogger().setLevel(args.log_level)
    client = Client(
        email=args.wyze_email, 
        password=args.wyze_password,
        key_id=args.wyze_key_id,
        api_key=args.wyze_api_key)
    ret = perform_action(client, to_list(args.include), to_list(args.exclude), args.action)
    sys.exit(ret)

except Exception as e:
    logging.critical(e)
    sys.exit(-1)
