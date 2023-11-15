"""
    Name: _Data_acquisition.py
    Author: Jakub Sukdol
    Date: 25.10.23

    Basic data acquisition class and functions
"""

__all__ = ["DataAcquisition", "save_data_to_database"]

import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine
from sshtunnel import SSHTunnelForwarder
from loguru import logger
from nba_api.stats.endpoints import leaguegamefinder
from typing import Optional
import time
from selenium import webdriver
from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.utils.wrappers import ssh_tunnel
from src.utils import check_input
from src.data_management._macros import *
from src.data_management._data_saving_loading import *


@ssh_tunnel
def save_data_to_database(df: pd.DataFrame, db_name: str, table: str, schema: str,
                          db_user: str, db_pwd: str, ssh_server: SSHTunnelForwarder) -> None:
    """
    Connect  to postgres database via SSH tunnelling and create table from df
    :param df: (pandas.Dataframe)
    :param db_name: (str) name of postgres database
    :param table: (str) name of table to be created / appended
    :param schema: (str) name of postgres schema
    :param db_user: (str) postgres db username
    :param db_pwd: (str) postgres db password
    :param ssh_server: ADDED BY DECORATOR! Don't include in function call as it will have no effect
    :return: (None)

    ! IMPORTANT !
    for Keyword arguments for ssh tunnel see documentation of the decorator
    """
    if df is None:
        raise ValueError("Cannot push NoneType dataframe")

    # connect to PostgreSQL
    local_port = str(ssh_server.local_bind_port)
    connect_string = f'postgresql://{db_user}:{db_pwd}@localhost:{local_port}/{db_name}'
    engine = create_engine(connect_string)
    logger.info(f"Postgres engine created for {connect_string}")

    df.to_sql(table, engine, schema=schema)
    logger.info("dataframe saved to databse")


class DataAcquisition:
    def __init__(self, out_path: str = PATH):
        self.fpath: str = out_path
        self.df: Optional[pd.DataFrame] = None

    def _data_nba_by_date(self, date_from: str, date_to: str) -> None:
        game_finder = leaguegamefinder.LeagueGameFinder(
            date_from_nullable=date_from, date_to_nullable=date_to, timeout=60)
        games = game_finder.get_data_frames()[0]
        self.df = games

    def _get_data_from_csv(self, fname: str):
        self.df = load_data_csv(fname)

    def get_data(self, o_from: int, **kwargs) -> pd.DataFrame:
        """
        acquire data from various sources
        :param o_from: flags FROM_CSV, FROM_FLASHSCORE, FROM_NBA_STATS

        :keyword date_from: (str) has to be specified when FROM_NBA_STATS flag is set
        :keyword date_to: (str) has to be specified when FROM_NBA_STATS flag is set

        :keyword fname: (str) has to be specified when FROM_CSV flag is set

        :keyword url: (str) has to be specified when FROM_FLASHSCORE flag is set
        :keyword year: (str) "yyyy-yyyy" (league year from, to)
            has to be specified when FROM_FLASHSCORE flag is set
        :keyword state: (str) has to be specified when FROM_FLASHSCORE flag is set
        :keyword league: (str) has to be specified when FROM_FLASHSCORE flag is set

        :return: (pd.Dataframe) Acquired data
        """
        if o_from & FROM_NBA_STATS:
            date_from, date_to = check_input(['date_from', 'date_to'], **kwargs)
            self._data_nba_by_date(date_from, date_to)
        elif o_from & FROM_CSV:
            fname = check_input(['fname'], **kwargs)[0]
            self._get_data_from_csv(fname)
        elif o_from & FROM_FLASHSCORE:
            url, year, state, league = check_input(['url', 'year', 'state', 'league'], **kwargs)
            self._get_flashscore_data(url, year, state, league)
        else:
            raise ValueError("Wrong Option flag o_from set")

        return self.df

    def save_data_to_database(self, *args, **kwargs):
        """ reference for simpler manipulation (for details see save_data_to_database doc) """
        save_data_to_database(*args, df=self.df, **kwargs)

    def safe_data_csv(self, fname: str):
        """ reference for simpler manipulation """
        safe_data_csv(df=self.df, fname=fname)

    def _get_flashscore_data(self, url: str, year_league: str = "", state: str = "", league: str = ""):
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        driver = Chrome(options=options)
        driver.implicitly_wait(5)
        driver.get(url)
        time.sleep(3) # give driver time to load the page
        self.df = pd.DataFrame(
            columns=["State", "League", "league_years", "DT", "Home", "Away", "Winner", "Home_points", "Away_points",
                     "H_14", "A_14", "H_24", "A_24", "H_34", "A_34", "H_44", "A_44", "H_54", "A_54"])

        while True:
            # load whole page
            try:
                more = driver.find_element(By.CLASS_NAME, "event__more.event__more--static")
                driver.execute_script("arguments[0].scrollIntoView();arguments[1].click();",
                    more, WebDriverWait(driver, 20).until(EC.element_to_be_clickable(more)))
                time.sleep(3)  # give driver time to load the page
            except:
                # no clickable element for loading more data on page found
                break

        matches = driver.find_elements(By.CLASS_NAME, "event__match.event__match--static.event__match--twoLine")
        last_month = -1
        dt_year = int(year_league[5:])
        for match in matches:
            # sats_page = f"https://www.flashscore.com/match/{}/#/match-summary/match-statistics/"
            match_text = match.text.split("\n")
            match_text.remove('AOT') if 'AOT' in match_text else ...
            if len(match_text) == 13:
                #  no extra points
                date_str, home, away, h_all, a_all, h14, a14, h24, a24, h34, a34, h44, a44 = match_text
                h54, a54 = 0, 0
            elif len(match_text) == 15:
                date_str, home, away, h_all, a_all, h14, a14, h24, a24, h34, a34, h44, a44, h54, a54 = match_text
            else:
                logger.error(f"Unknown type of div element parsed, skipping.\nmatch_text = {match_text}")
                continue

            winner = "home" if h_all > a_all else "away" if a_all > h_all else "draw"
            index_space = date_str.index(' ')
            date_str = date_str[:index_space] + date_str[index_space:]
            dt = datetime.strptime(date_str, '%d.%m. %H:%M')

            month = dt.month
            if last_month == 1 and month == 12:
                dt_year = year_league[:4]

            last_month = month

            dt = dt.replace(year=dt_year)

            row = [
                state, league, year_league, dt, home, away, winner,
                h_all, a_all, h14, a14, h24, a24, h34, a34, h44, a44, h54, a54
            ]
            row = row[:7] + list(map(int, row[7:]))
            self.df.loc[len(self.df.index)] = row

        if len(self.df) > 0:
            logger.info(f"Successfully parsed {len(self.df)} matches from {url}")
        else:
            logger.info(f"Parsing matches from {url} was unsuccessful")