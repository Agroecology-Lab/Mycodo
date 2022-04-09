# coding=utf-8
#
# on_off_kp303_0_4_2_test_03.py - Output for KP303
#
import asyncio
import random
import threading
import time
import traceback
from flask_babel import lazy_gettext

from mycodo.config_translations import TRANSLATIONS
from mycodo.databases.models import OutputChannel
from mycodo.outputs.base_output import AbstractOutput
from mycodo.utils.constraints_pass import constraints_pass_positive_value
from mycodo.utils.database import db_retrieve_table_daemon

# Measurements
measurements_dict = {
    key: {
        'measurement': 'duration_time',
        'unit': 's'
    }
    for key in range(3)
}

channels_dict = {
    key: {
        'types': ['on_off'],
        'name': f'Outlet {key + 1}',
        'measurements': [key]
    }
    for key in range(3)
}

# Output information
OUTPUT_INFORMATION = {
    'output_name_unique': 'kp303_0_4_2_alt_02',
    'output_name': f"{lazy_gettext('On/Off')}: KP303 Kasa 3-Outlet WiFi Power Strip (python-kasa 0.4.2, TEST #3)",
    'output_manufacturer': 'TP-Link',
    'input_library': 'python-kasa==0.4.2',
    'measurements_dict': measurements_dict,
    'channels_dict': channels_dict,
    'output_types': ['on_off'],

    'url_manufacturer': 'https://www.tp-link.com/au/home-networking/smart-plug/kp303/',

    'message': 'This output controls the 3 outlets of the Kasa KP303 Smart WiFi Power Strip. This is a variant that uses the latest python-kasa library.',

    'options_enabled': [
        'button_on',
        'button_send_duration'
    ],
    'options_disabled': ['interface'],

    'dependencies_module': [
        ('pip-pypi', 'kasa', 'python-kasa==0.4.2'),
        ('pip-pypi', 'aio_msgpack_rpc', 'aio_msgpack_rpc==0.2.0')
    ],

    'interfaces': ['IP'],

    'custom_options': [
        {
            'id': 'plug_address',
            'type': 'text',
            'default_value': '192.168.0.50',
            'required': True,
            'name': TRANSLATIONS['host']['title'],
            'phrase': TRANSLATIONS['host']['phrase']
        },
        {
            'id': 'status_update_period',
            'type': 'integer',
            'default_value': 300,
            'constraints_pass': constraints_pass_positive_value,
            'required': True,
            'name': 'Status Update (seconds)',
            'phrase': 'The period (seconds) between checking if connected and output states. 0 disables.'
        }
    ],

    'custom_channel_options': [
        {
            'id': 'name',
            'type': 'text',
            'default_value': 'Outlet Name',
            'required': True,
            'name': TRANSLATIONS['name']['title'],
            'phrase': TRANSLATIONS['name']['phrase']
        },
        {
            'id': 'state_startup',
            'type': 'select',
            'default_value': 0,
            'options_select': [
                (-1, 'Do Nothing'),
                (0, 'Off'),
                (1, 'On')
            ],
            'name': lazy_gettext('Startup State'),
            'phrase': 'Set the state when Mycodo starts'
        },
        {
            'id': 'state_shutdown',
            'type': 'select',
            'default_value': 0,
            'options_select': [
                (-1, 'Do Nothing'),
                (0, 'Off'),
                (1, 'On')
            ],
            'name': lazy_gettext('Shutdown State'),
            'phrase': 'Set the state when Mycodo shuts down'
        },
        {
            'id': 'trigger_functions_startup',
            'type': 'bool',
            'default_value': False,
            'name': lazy_gettext('Trigger Functions at Startup'),
            'phrase': 'Whether to trigger functions when the output switches at startup'
        },
        {
            'id': 'command_force',
            'type': 'bool',
            'default_value': False,
            'name': lazy_gettext('Force Command'),
            'phrase': 'Always send the command if instructed, regardless of the current state'
        },
        {
            'id': 'amps',
            'type': 'float',
            'default_value': 0.0,
            'required': True,
            'name': '{} ({})'.format(lazy_gettext('Current'), lazy_gettext('Amps')),
            'phrase': 'The current draw of the device being controlled'
        }
    ]
}


class OutputModule(AbstractOutput):
    """An output support class that operates the Kasa KP303 and HS300 WiFi Power Strips."""
    def __init__(self, output, testing=False):
        super().__init__(output, testing=testing, name=__name__)

        self.strip = None
        self.rpc_server_thread = None
        self.port = None
        self.status_thread = None
        self.timer_status_check = time.time()

        self.plug_address = None
        self.status_update_period = None

        self.setup_custom_options(
            OUTPUT_INFORMATION['custom_options'], output)

        output_channels = db_retrieve_table_daemon(
            OutputChannel).filter(OutputChannel.output_id == self.output.unique_id).all()
        self.options_channels = self.setup_custom_channel_options_json(
            OUTPUT_INFORMATION['custom_channel_options'], output_channels)

    def initialize(self):
        self.setup_output_variables(OUTPUT_INFORMATION)

        if not self.plug_address:
            self.logger.error("Plug address must be set")
            return

        self.port = 18000 + random.randint(0, 300)

        loop = asyncio.new_event_loop()
        self.rpc_server_thread = threading.Thread(
            target=self.aio_rpc_server, args=(loop, self.logger, len(channels_dict)))
        self.rpc_server_thread.start()

        time.sleep(1)

        self.connect()

        if self.output_setup:
            if self.status_update_period:
                self.status_thread = threading.Thread(target=self.status_update)
                self.status_thread.start()

            for channel in range(len(channels_dict)):
                if self.options_channels['state_startup'][channel] == 1:
                    self.outlet_change(channel, True)
                elif self.options_channels['state_startup'][channel] == 0:
                    self.outlet_change(channel, False)


    def aio_rpc_server(self, loop, logger, channels):
        import aio_msgpack_rpc
        from kasa import SmartStrip

        class KasaServer:
            """Communicates with the Kasa power strip"""
            def __init__(self, address_, channels_):
                self.strip = None
                self.address = address_
                self.channels = channels_

            async def connect(self):
                try:
                    self.strip = SmartStrip(self.address)
                    await self.strip.update()
                    return 0, f'Strip {self.strip.alias}: {self.strip.hw_info}'
                except Exception:
                    return 1, str(traceback.print_exc())

            async def outlet_on(self, channel):
                try:
                    await self.strip.children[channel].turn_on()
                    return 0, "success"
                except Exception:
                    return 1, str(traceback.print_exc())

            async def outlet_off(self, channel):
                try:
                    await self.strip.children[channel].turn_off()
                    return 0, "success"
                except Exception:
                    return 1, str(traceback.print_exc())

            async def get_status(self):
                try:
                    await self.strip.update()
                    channel_stat = []
                    for channel in range(self.channels):
                        if self.strip.children[channel].is_on:
                            channel_stat.append(True)
                        else:
                            channel_stat.append(False)
                    return 0, channel_stat
                except Exception:
                    return 1, str(traceback.print_exc())

        async def main(address, port, channels_):
            try:
                server = await asyncio.start_server(
                    aio_msgpack_rpc.Server(KasaServer(address, channels_)),
                    host="127.0.0.1", port=port)

                while True:
                    await asyncio.sleep(0.1)
            except Exception:
                logger.exception("server")
            finally:
                server.close()

        logger.info("starting server")

        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(main(self.plug_address, self.port, channels))
        except Exception:
            logger.exception("server")
        except KeyboardInterrupt:
            pass

        logger.info("server ended")

    def connect(self):
        import aio_msgpack_rpc

        event_loop_a = asyncio.new_event_loop()

        async def connect(port):
            client = aio_msgpack_rpc.Client(*await asyncio.open_connection("localhost", port))
            status, msg = await client.call("connect")
            if status:
                self.logger.error(f"Connecting: {msg}")
            else:
                self.logger.debug(f"Connecting: Error: {msg}")

        asyncio.set_event_loop(event_loop_a)
        asyncio.get_event_loop()
        event_loop_a.run_until_complete(connect(self.port))

        self.output_setup = True

    def outlet_change(self, channel, state):
        import aio_msgpack_rpc

        event_loop_a = asyncio.new_event_loop()

        async def outlet_change(port, channel_, state_):
            client = aio_msgpack_rpc.Client(*await asyncio.open_connection("localhost", port))

            if state_:
                status, msg = await client.call("outlet_on", channel_)
            else:
                status, msg = await client.call("outlet_off", channel_)

            if status:
                self.logger.error(f"Switching CH{channel_} {'ON' if state_ else 'OFF'}: {msg}")
                self.output_states[channel] = state
            else:
                self.logger.debug(f"Switching CH{channel_} {'ON' if state_ else 'OFF'}: Error: {msg}")

        asyncio.set_event_loop(event_loop_a)
        asyncio.get_event_loop()
        event_loop_a.run_until_complete(outlet_change(self.port, channel, state))

    def status_update(self):
        import aio_msgpack_rpc

        while self.running:
            if self.timer_status_check < time.time():
                while self.timer_status_check < time.time():
                    self.timer_status_check += self.status_update_period

                self.logger.debug("Checking state of outlets")

                try:
                    event_loop_a = asyncio.new_event_loop()

                    async def get_status(port):
                        client = aio_msgpack_rpc.Client(*await asyncio.open_connection("localhost", port))

                        status, msg = await client.call("get_status")
                        if status:
                            self.logger.error(f"Status: {msg}")
                        else:
                            self.logger.debug(f"Status: Error: {msg}")
                        if msg:
                            for channel, state in enumerate(msg):
                                self.output_states[channel] = state

                    asyncio.set_event_loop(event_loop_a)
                    asyncio.get_event_loop()
                    event_loop_a.run_until_complete(get_status(self.port))
                except Exception as e:
                    self.logger.error(f"Could not query power strip status: {e}")

            time.sleep(1)

    def output_switch(self, state, output_type=None, amount=None, output_channel=None):
        if not self.is_setup():
            msg = "Error 101: Device not set up. See https://kizniche.github.io/Mycodo/Error-Codes#error-101 for more info."
            self.logger.error(msg)
            return msg

        try:
            if state == 'on':
                self.outlet_change(output_channel, True)
            elif state == 'off':
                self.outlet_change(output_channel, False)
        except Exception as err:
            self.logger.exception(f"State change error: {err}")

    def is_on(self, output_channel=None):
        if self.is_setup():
            if output_channel is not None and output_channel in self.output_states:
                return self.output_states[output_channel]
            else:
                return self.output_states

    def is_setup(self):
        return self.output_setup

    def stop_output(self):
        """Called when Output is stopped."""
        if self.is_setup():
            for channel in channels_dict:
                if self.options_channels['state_shutdown'][channel] == 1:
                    self.output_switch('on', output_channel=channel)
                elif self.options_channels['state_shutdown'][channel] == 0:
                    self.output_switch('off', output_channel=channel)
        self.running = False