import os
import sys
import json
import time
import logging
import requests
import pandas as pd
from io import StringIO
import reporting.utils as utl
from requests_oauthlib import OAuth2Session

config_path = utl.config_path
base_url = 'https://www.googleapis.com/dfareporting'


class DcApi(object):
    default_fields = [
        'dfa:campaign', 'dfa:campaignId', 'dfa:site', 'dfa:placement',
        'dfa:date', 'dfa:placementId', 'dfa:creative', 'dfa:ad']
    default_metrics = [
        'dfa:impressions', 'dfa:clicks', 'dfa:clickRate',
        'dfa:activeViewViewableImpressions',
        'dfa:activeViewMeasurableImpressions',
        'dfa:activeViewEligibleImpressions', 'dfa:totalConversions',
        'dfa:dbmCost', 'dfa:dbmCostUsd', 'dfa:mediaCost',
        'dfa:totalConversionsRevenue', 'dfa:richMediaVideoViews',
        'dfa:richMediaTrueViewViews', 'dfa:richMediaVideoCompletions',
        'dfa:richMediaVideoThirdQuartileCompletes',
        'dfa:richMediaVideoMidpoints',
        'dfa:richMediaVideoFirstQuartileCompletes', 'dfa:richMediaVideoPlays']
    default_conversion_metrics = [
        'dfa:activityClickThroughConversions', 'dfa:totalConversions',
        'dfa:totalConversionsRevenue', 'dfa:activityViewThroughConversions',
        'dfa:activityViewThroughRevenue', 'dfa:activityClickThroughRevenue']

    def __init__(self):
        self.config = None
        self.config_file = None
        self.client_id = None
        self.client_secret = None
        self.access_token = None
        self.refresh_token = None
        self.refresh_url = None
        self.usr_id = None
        self.advertiser_id = None
        self.campaign_id = None
        self.report_id = None
        self.config_list = None
        self.client = None
        self.version = '3.3'
        self.df = pd.DataFrame()
        self.r = None

    def input_config(self, config):
        if str(config) == 'nan':
            logging.warning('Config file name not in vendor matrix.  '
                            'Aborting.')
            sys.exit(0)
        logging.info('Loading DC config file: {}'.format(config))
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
        self.client_id = self.config['client_id']
        self.client_secret = self.config['client_secret']
        self.access_token = self.config['access_token']
        self.refresh_token = self.config['refresh_token']
        self.refresh_url = self.config['refresh_url']
        self.usr_id = self.config['usr_id']
        self.report_id = self.config['report_id']
        self.config_list = [self.config, self.client_id, self.client_secret,
                            self.refresh_token, self.refresh_url, self.usr_id]
        if 'advertiser_id' in self.config:
            self.advertiser_id = self.config['advertiser_id']
        if 'campaign_id' in self.config:
            self.campaign_id = self.config['campaign_id']

    def check_config(self):
        for item in self.config_list:
            if item == '':
                logging.warning('{} not in DC config file.'
                                'Aborting.'.format(item))
                sys.exit(0)

    def refresh_client_token(self, extra, attempt=1):
        try:
            token = self.client.refresh_token(self.refresh_url, **extra)
        except requests.exceptions.ConnectionError as e:
            attempt += 1
            if attempt > 100:
                logging.warning('Max retries exceeded: {}'.format(e))
                token = None
            else:
                logging.warning('Connection error retrying 60s: {}'.format(e))
                token = self.refresh_client_token(extra, attempt)
        return token

    def get_client(self):
        token = {'access_token': self.access_token,
                 'refresh_token': self.refresh_token,
                 'token_type': 'Bearer',
                 'expires_in': 3600,
                 'expires_at': 1504135205.73}
        extra = {'client_id': self.client_id,
                 'client_secret': self.client_secret}
        self.client = OAuth2Session(self.client_id, token=token)
        token = self.refresh_client_token(extra)
        self.client = OAuth2Session(self.client_id, token=token)

    def create_user_url(self):
        vers_url = '/v{}'.format(self.version)
        usr_url = '/userprofiles/{}/'.format(self.usr_id)
        user_url = '{}{}{}'.format(base_url, vers_url, usr_url)
        return user_url

    def create_url(self):
        user_url = self.create_user_url()
        report_url = 'reports/{}'.format(self.report_id)
        full_url = (user_url + report_url)
        return full_url

    def get_data(self, sd=None, ed=None, fields=None):
        self.create_report()
        files_url = self.get_files_url()
        self.r = self.get_report(files_url)
        if not self.r:
            return pd.DataFrame()
        self.df = self.get_df_from_response()
        return self.df

    def find_first_line(self):
        for idx, x in enumerate(self.r.text.splitlines()):
            if idx > 1000:
                logging.warning('Could not find first line, returning empty df')
                return None
            if x == 'Report Fields':
                return idx

    def get_df_from_response(self):
        first_line = self.find_first_line()
        if first_line:
            self.df = pd.read_csv(StringIO(self.r.text), skiprows=first_line)
            self.df = self.df.reset_index()
        else:
            self.df = pd.DataFrame()
        return self.df

    def get_report(self, files_url):
        for x in range(1, 401):
            report_status = self.check_file(files_url, attempt=x)
            if report_status:
                break
        if not report_status:
            logging.warning('Report could not download returning blank df.')
            return None
        logging.info('Report available.  Downloading.')
        report_url = '{}?alt=media'.format(files_url)
        self.r = self.make_request(report_url, 'get', json_response=False)
        return self.r

    def check_file(self, files_url, attempt=1):
        r = self.make_request(files_url, 'get')
        if 'status' in r.json() and r.json()['status'] == 'REPORT_AVAILABLE':
            return True
        else:
            logging.info('Report unavailable.  Attempt {}.  '
                         'Response: {}'.format(attempt, r.json()))
            time.sleep(30)
            return False

    def get_files_url(self):
        full_url = self.create_url()
        self.r = self.make_request('{}/run'.format(full_url), 'post')
        file_id = self.r.json()['id']
        files_url = '{}/files/{}'.format(full_url, file_id)
        return files_url

    def make_request(self, url, method, body=None, params=None, attempt=1,
                     json_response=True):
        self.get_client()
        try:
            self.r = self.raw_request(url, method, body=body, params=params)
        except requests.exceptions.SSLError as e:
            logging.warning('Warning SSLError as follows {}'.format(e))
            time.sleep(30)
            self.r = self.make_request(url=url, method=method, body=body,
                                       params=params, attempt=attempt,
                                       json_response=json_response)
        if json_response and 'error' in self.r.json():
            logging.warning('Request error.  Retrying {}'.format(self.r.json()))
            time.sleep(30)
            attempt += 1
            if attempt > 10:
                self.request_error()
            self.r = self.make_request(url=url, method=method, body=body,
                                       params=params, attempt=attempt,
                                       json_response=json_response)
        return self.r

    def raw_request(self, url, method, body=None, params=None):
        if method == 'get':
            if body:
                self.r = self.client.get(url, json=body, params=params)
            else:
                self.r = self.client.get(url, params=params)
        elif method == 'post':
            if body:
                self.r = self.client.post(url, json=body, params=params)
            else:
                self.r = self.client.post(url, params=params)
        return self.r

    def request_error(self):
        logging.warning('Unknown error: {}'.format(self.r.text))
        sys.exit(0)

    def create_report(self):
        if self.report_id:
            return
        report = self.create_report_params()
        full_url = self.create_url()
        self.r = self.make_request(full_url, method='post', body=report)
        self.report_id = self.r.json()['id']
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f)

    def get_floodlight_tag_ids(self, fl_ids=None, next_page=None):
        if not fl_ids:
            fl_ids = []
        fl_url = self.create_user_url()
        params = {'advertiserId': self.advertiser_id}
        fl_url = '{}floodlightActivities'.format(fl_url)
        if next_page:
            params['pageToken'] = next_page
        self.r = self.make_request(fl_url, method='get', params=params)
        if 'floodlightActivities' not in self.r.json():
            logging.warning('floodlightActivities not in response as follows: '
                            '\n{}'.format(self.r.json()))
        fl_ids.extend([x['id'] for x in self.r.json()['floodlightActivities']])
        if 'nextPageToken' in self.r.json() and self.r.json()['nextPageToken']:
            fl_ids = self.get_floodlight_tag_ids(
                fl_ids, next_page=self.r.json()['nextPageToken'])
        return fl_ids

    def create_report_params(self):
        report_name = ('{}_{}_standard_report'
                       ''.format(self.advertiser_id,
                                 self.campaign_id.replace(',', '-')))
        report = {
            'name': report_name,
            'type': 'STANDARD',
            'fileName': report_name,
            'format': 'CSV'
        }
        criteria = self.create_report_criteria()
        report['criteria'] = criteria
        return report

    def create_report_criteria(self):
        criteria = {
            'dateRange': {'kind': 'dfareporting#dateRange',
                          'relativeDateRange': 'LAST_365_DAYS'},
            'dimensions': [{'kind': 'dfareporting#sortedDimension', 'name': x}
                           for x in self.default_fields],
            'metricNames': self.default_metrics,
            'activities': {
                'metricNames': self.default_conversion_metrics,
                'kind': 'dfareporting#activities',
                'filters': []
            },
            'dimensionFilters': [{
                'dimensionName': 'dfa:advertiser',
                'id': self.advertiser_id,
                'kind': 'dfareporting#dimensionValue'}]
        }
        fl_ids = self.get_floodlight_tag_ids()
        if fl_ids:
            criteria['activities']['filters'] = [
                {'dimensionName': 'dfa:activity',
                 'id': x,
                 'kind': 'dfareporting#dimensionValue'} for x in fl_ids]
        else:
            logging.warning('No floodlight conversions found.')
            criteria.pop('activities')
        if self.campaign_id:
            campaign_filters = [
                {'dimensionName': 'dfa:campaign',
                 'id': x,
                 'kind': 'dfareporting#dimensionValue'}
                for x in self.campaign_id.split(',')]
            criteria['dimensionFilters'].extend(campaign_filters)
        return criteria
