import io
import os
import sys
import json
import time
import logging
import requests
import datetime as dt
import pandas as pd
import reporting.utils as utl

config_path = utl.config_path

base_url = 'https://hq.appsflyer.com/export/'

report_types = ['geo_by_date_report', 'partners_by_date_report',
                'installs_report', 'in_app_events_report', 'geo_report',
                'daily_report', 'partners_report']


class AfApi(object):
    def __init__(self):
        self.config = None
        self.config_file = None
        self.api_token = None
        self.app_id = None
        self.config_list = None
        self.df = pd.DataFrame()
        self.r = None

    def input_config(self, config):
        if str(config) == 'nan':
            logging.warning('Config file name not in vendor matrix.  '
                            'Aborting.')
            sys.exit(0)
        logging.info('Loading AF config file: {}'.format(config))
        self.config_file = os.path.join(config_path, config)
        self.load_config()
        self.check_config()

    def load_config(self):
        try:
            with open(self.config_file, 'r') as f:
                self.config = json.load(f)
        except IOError:
            logging.error('{} not found.  Aborting.'.format(self.config_file))
            sys.exit(0)
        self.api_token = self.config['api_token']
        self.app_id = self.config['app_id']
        self.config_list = [self.config, self.api_token, self.app_id]

    def check_config(self):
        for item in self.config_list:
            if item == '':
                logging.warning('{} not in AF config file.  '
                                'Aborting.'.format(item))
                sys.exit(0)

    @staticmethod
    def parse_fields(items):
        sources = []
        category = None
        field = None
        for item in items:
            if item in report_types:
                field = item
            elif item == 'standard':
                category = 'standard'
            else:
                sources.append(item)
        if not field:
            field = 'partners_by_date_report'
        return field, sources, category

    @staticmethod
    def get_data_default_check(sd, ed):
        if sd is None:
            sd = dt.datetime.today() - dt.timedelta(days=1)
        if ed is None:
            ed = dt.datetime.today() - dt.timedelta(days=1)
        return sd, ed

    def create_url(self, sd, ed, field, sources,  category):
        report_url = '/{}/v5?'.format(field)
        token_url = 'api_token={}'.format(self.api_token)
        sded_url = '&from={}&to={}'.format(sd, ed)
        tz_url = '&timezone=America/Los_Angeles'
        full_url = (base_url + self.app_id + report_url + token_url +
                    sded_url + tz_url)
        if sources:
            source_url = '&media_source={}'.format(','.join(sources))
            full_url += source_url
        if category:
            cat_url = '&category={}'.format(category)
            full_url += cat_url
        return full_url

    def get_data(self, sd=None, ed=None, fields=None):
        sd, ed = self.get_data_default_check(sd, ed)
        if sd > ed:
            logging.warning('Start date greater than end date.  Start date '
                            'was set to end date.')
            sd = ed
        sd = dt.datetime.strftime(sd, '%Y-%m-%d')
        ed = dt.datetime.strftime(ed, '%Y-%m-%d')
        field, sources, category = self.parse_fields(fields)
        self.df = self.get_raw_data(sd, ed, field, sources, category)
        return self.df

    def get_raw_data(self, sd, ed, field, sources, category):
        full_url = self.create_url(sd, ed, field, sources, category)
        self.r = requests.get(full_url)
        if self.r.status_code == 200:
            df = self.data_to_df(self.r)
        else:
            self.request_error()
            df = self.get_raw_data(sd, ed, field, sources, category)
        return df

    def request_error(self):
        limit_error = 'Limit reached for'
        if self.r.status_code == 403 and self.r.text[:17] == limit_error:
            logging.warning('Limit reached pausing for 120 seconds.')
            time.sleep(120)
        elif self.r.status_code == 504:
            logging.warning('Gateway timeout.  Pausing for 120 seconds.')
            time.sleep(120)
        else:
            logging.warning('Unknown error: {}'.format(self.r.text))
            sys.exit(0)

    @staticmethod
    def data_to_df(r):
        df = pd.read_csv(io.StringIO(r.text))
        return df
