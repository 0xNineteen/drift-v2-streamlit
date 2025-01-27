import sys
from tokenize import tabsize
import driftpy
import pandas as pd 
import numpy as np 

pd.options.plotting.backend = "plotly"

import time
# from driftpy.constants.config import configs
from anchorpy import Provider, Wallet
from solana.keypair import Keypair
from solana.rpc.async_api import AsyncClient
from driftpy.clearing_house import ClearingHouse
from driftpy.accounts import get_perp_market_account, get_spot_market_account, get_user_account, get_state_account
from driftpy.constants.numeric_constants import * 
import os
import json
import streamlit as st
from driftpy.constants.banks import devnet_banks, Bank
from driftpy.constants.markets import devnet_markets, Market
from dataclasses import dataclass
from solana.publickey import PublicKey
from helpers import serialize_perp_market_2, serialize_spot_market
from anchorpy import EventParser
import asyncio

import requests
from aiocache import Cache
from aiocache import cached
from driftpy.types import *
from driftpy.addresses import * 
from driftpy.constants.numeric_constants import *
import plotly.graph_objs as go
from plotly.subplots import make_subplots

import asyncio

@st.experimental_memo
def cached_get_orders_data(rpc: str, _ch: ClearingHouse, depth_slide, market_type, market_index):
    loop = asyncio.new_event_loop()
    return loop.run_until_complete(get_orders_data(rpc, _ch, depth_slide, market_type, market_index))

@st.experimental_memo
def cached_get_price_data(market_type, market_index):
    loop = asyncio.new_event_loop()
    return loop.run_until_complete(get_price_data(market_type, market_index))


async def get_price_data(market_type, market_index):
    assert(market_type in ['spot', 'perp'])
    url = f'https://mainnet-beta.api.drift.trade/trades?marketIndex={market_index}&marketType={market_type}'
    dat = requests.get(url).json()['data']['trades']
    return pd.DataFrame(dat)

async def get_orders_data(rpc: str, _ch: ClearingHouse, depth_slide, market_type, market_index):
    all_users = await _ch.program.account['User'].all()

    st.sidebar.text('cached on: ' + _ch.time)
    
    # long orders 
    # short orders 
    # current oracle price 
    # [bids/longs] [asks/shorts]
    # [price, baa], [price, baa]
    # (dec price)    (inc price)
    from driftpy.clearing_house_user import get_oracle_data

    state = await get_state_account(_ch.program)
    mm = state.number_of_markets if market_type == 'perp' else state.number_of_spot_markets
    for perp_idx in range(mm):
        if perp_idx != market_index:
            continue
        market = None
        oracle_pubkey = None
        if market_type == 'perp':
            market = await get_perp_market_account(
                _ch.program, perp_idx
            )
            oracle_pubkey = market.amm.oracle
        else:
            market = await get_spot_market_account(
                _ch.program, perp_idx
            )
            oracle_pubkey = market.oracle

        oracle_data = (await get_oracle_data(_ch.program.provider.connection, oracle_pubkey))
        oracle_price = oracle_data.price
        # oracle_price = 14 * 1e6

        order_type_dict = {'PositionDirection.Long()': [], 'PositionDirection.Short()': []}
        for x in all_users: 
            user: User = x.account
            orders = user.orders
            for order in orders:
                if str(order.status) == 'OrderStatus.Open()' and order.market_index == perp_idx:
                    order.owner = str(x.public_key)
                    order.authority = str(x.account.authority)

                    # if order.trigger_price != 0 and order.price == 0: 
                    #     order.price = order.trigger_price
                    
                    if order.oracle_price_offset != 0 and order.price == 0: 
                        order.price = oracle_price + order.oracle_price_offset

                    # oracle offset orders for now 
                    if order.price != 0:
                        order_type = str(order.direction)
                        order_type_dict[order_type] = order_type_dict.get(order_type, []) + [order]

        longs = order_type_dict['PositionDirection.Long()']
        longs.sort(key=lambda order: order.price)
        longs = longs[::-1] # decreasing price 

        shorts = order_type_dict['PositionDirection.Short()']
        shorts.sort(key=lambda order: order.price) # increasing price

        def format_order(order: Order):
            price = float(f'{order.price/PRICE_PRECISION:,.4f}')
            size = (order.base_asset_amount - order.base_asset_amount_filled)/AMM_RESERVE_PRECISION
            return (price, size)

        d_longs_authority = [str(order.authority) for order in longs]
        d_longs_order_id = [order.order_id for order in longs]
        d_longs_owner = [str(order.owner) for order in longs]
        d_longs_order_type = [str(order.order_type).split('.')[-1].split('()')[0] for order in longs]
        d_longs_order_type = [x+' [POST]' if longs[idx].post_only else x for idx, x in enumerate(d_longs_order_type)]
        d_longs_order_type = ['Oracle'+x if longs[idx].oracle_price_offset != 0 else x for idx, x in enumerate(d_longs_order_type)]
        d_longs = [format_order(order) for order in longs]
        d_shorts = [format_order(order) for order in shorts]
        d_shorts_order_type = [str(order.order_type).split('.')[-1].split('()')[0] for order in shorts]
        d_shorts_order_type = [x+' [POST]' if shorts[idx].post_only else x for idx, x in enumerate(d_shorts_order_type)]
        d_shorts_order_type = ['Oracle'+x if shorts[idx].oracle_price_offset != 0 else x for idx, x in enumerate(d_shorts_order_type)]
        d_shorts_owner = [str(order.owner) for order in shorts]
        d_shorts_authority = [str(order.authority) for order in shorts]
        d_shorts_order_id = [order.order_id for order in shorts]

        # st.write(f'number of bids: {len(d_longs)}')
        # st.write(f'number of asks: {len(d_shorts)}')

        # col1, col2, col3 = st.columns(3)
        # col1.metric("best bid", d_longs[0][0], str(len(d_longs))+" orders total")
        # col2.metric("best ask",  d_shorts[0][0], "-"+str(len(d_shorts))+" orders total")

        pad = abs(len(d_longs) - len(d_shorts))
        if len(d_longs) > len(d_shorts):
            d_shorts += [""] * pad
            d_shorts_owner += [""] * pad
            d_shorts_order_type += [""] * pad
            d_shorts_authority += [""] * pad
            d_shorts_order_id += [""] * pad
        else:
            d_longs += [""] * pad
            d_longs_owner  += [""] * pad
            d_longs_order_type += [""] * pad
            d_longs_authority += [""] * pad
            d_longs_order_id += [""] * pad

        market_name = bytes(market.name).decode('utf-8')

        order_data = {
            'market': market_name,
            'bids order id': d_longs_order_id,
            'bids authority': d_longs_authority,
            'bids owner': d_longs_owner,
            'bids order type': d_longs_order_type,
            'bids (price, size)': d_longs,
            'asks (price, size)': d_shorts,
            'asks order type': d_shorts_order_type,
            'asks owner': d_shorts_owner,
            'asks authority': d_shorts_authority,
            'asks order id': d_shorts_order_id,
        }


        price_min = float(oracle_price/1e6)*float(1-depth_slide*.001)
        price_max = float(oracle_price/1e6)*float(1+depth_slide*.001)
        drift_depth = pd.DataFrame(columns=['bids', 'asks'])
        drift_order_depth = pd.DataFrame(columns=['bids', 'asks'])
        if market_type == 'perp':
            drift_order_depth, drift_depth = calc_drift_depth(oracle_price/1e6, market.amm.long_spread/1e6, 
            
            market.amm.short_spread/1e6,
            market.amm.base_asset_reserve/1e9, price_max, order_data)
            
        return (pd.DataFrame(order_data), oracle_data, drift_order_depth, drift_depth)


def calc_drift_depth(mark_price, l_spr, s_spr, base_asset_reserve, price_max, order_data):
        def calc_slip(x):
            f = x/base_asset_reserve
            slippage = 1/(1-f)**2 - 1
            return slippage
        def calc_slip_short(x):
            f = x/base_asset_reserve
            slippage = 1 - 1/(1+f)**2
            return slippage

        max_f = np.sqrt(price_max)/np.sqrt(mark_price) - 1

        # st.table(pd.Series([max_f, mark_price, price_max, base_asset_reserve]))
        quantities_max = max(1, int(max_f*base_asset_reserve))
        quantities = list(range(1, quantities_max, int(max(1, quantities_max/100))))
        drift_asks = pd.DataFrame(quantities, 
        columns=['asks'],
        index=[mark_price*(1+l_spr)*(1+calc_slip(x)) for x in quantities])
        drift_bids = pd.DataFrame(quantities, 
        columns=['bids'],
        index=[mark_price*(1-s_spr)*(1-calc_slip_short(x)) for x in quantities])

        # print(order_data['bids (price, size)'])
        drift_order_bids = pd.DataFrame(order_data['bids (price, size)'], columns=['price', 'bids'])\
            .set_index('price').dropna()
        drift_order_bids['bids'] = drift_order_bids['bids'].astype(float).cumsum()
        drift_order_bids.rename_axis(None, inplace=True)
       
        drift_order_asks = pd.DataFrame(order_data['asks (price, size)'], columns=['price', 'asks'])\
            .set_index('price').dropna()
        drift_order_asks['asks'] = drift_order_asks['asks'].astype(float).cumsum()
        drift_order_asks.rename_axis(None, inplace=True)
        drift_order_asks = drift_order_asks.loc[:price_max]

        drift_depth = pd.concat([drift_bids, drift_asks]).replace(0, np.nan).sort_index()

        drift_order_depth = pd.concat([drift_order_bids, drift_order_asks]).replace(0, np.nan).sort_index()
        ss = [drift_depth.index.min()]+drift_order_depth.index.to_list()+[drift_depth.index.max()]
        drift_order_depth = drift_order_depth.reindex(ss, method='ffill').sort_index()


        return drift_order_depth, drift_depth



def orders_page(rpc: str, ch: ClearingHouse):

        # time.sleep(3)
        # oracle_price = 13.5 * 1e6 

        depth_slide = 10

        market = st.radio('select market:', ['SOL-PERP', 'SOL-USDC'], horizontal=True)

        if market == 'SOL-PERP':
            market_type = 'perp'
            market_index= 0
        else:
            market_type = 'spot'
            market_index = 1

        data, oracle_data, drift_order_depth, drift_depth  = cached_get_orders_data(rpc, ch, depth_slide, market_type, market_index)
        # if len(data):
        #     st.write(f'{data.market.values[0]}')

        zol1, zol2, zol3 = st.columns([1,6,20])

        zol1.image("https://alpha.openserum.io/api/serum/token/So11111111111111111111111111111111111111112/icon", width=33)
        # print(oracle_data)
        oracle_data.slot
        zol2.metric('Oracle Price', f'${oracle_data.price/PRICE_PRECISION}', f'±{oracle_data.confidence/PRICE_PRECISION} (slot={oracle_data.slot})',
        delta_color="off")
        tabs = st.tabs(['OrderBook', 'Depth', 'Recent Trades'])

        with tabs[0]:


            correct_order = data.columns.tolist()
            cols = st.multiselect(
                            "Choose columns", data.columns.tolist(), 
                            ['bids order id', 'bids order type', 'bids (price, size)', 'asks (price, size)', 'asks order type',  'asks order id']
                        )
            subset_ordered = [x for x in correct_order if x in cols]
            df = pd.DataFrame(data)[subset_ordered]

            def make_clickable(link):
                # target _blank to open new window
                # extract clickable text to display for your link
                text = link.split('=')[1]
                text = text[:4]+'..'+text[-4:]
                return f'<a target="_blank" href="{link}">{text}</a>'

            # link is the column with hyperlinks
            # df['link'] = df['bids authority'].apply(lambda x: f'https://app.drift.trade/?authority={x}')
            # df['link'] = df['link'].apply(make_clickable)

            def highlight_survived(s):
                res = []
                for _ in range(len(s)):
                    if 'bids (price' in s.name:
                        res.append('background-color: lightgreen')
                    elif 'asks (price' in s.name:
                        res.append('background-color: pink')
                    else:
                        res.append('')
                return res

            bids_quote = np.round(df['bids (price, size)'].apply(lambda x: x[0]*x[1] if x!='' else 0).sum(), 2)
            bids_base = np.round(df['bids (price, size)'].apply(lambda x: x[1] if x!='' else 0).sum(), 2)

            asks_quote = np.round(df['asks (price, size)'].apply(lambda x: x[0]*x[1] if x!='' else 0).sum(), 2)
            asks_base = np.round(df['asks (price, size)'].apply(lambda x: x[1] if x!='' else 0).sum(), 2)
            col1, col2, _ = st.columns([5,5, 10])
            col1.metric(f'bids:', f'${bids_quote}', f'{bids_base} SOL')
            col2.metric(f'asks:', f'${asks_quote}', f'{-asks_base} SOL')
            if len(df):
                st.dataframe(df.style.apply(highlight_survived, axis=0))
            else:
                st.dataframe(df)

        with tabs[1]: 
            # depth_slide = st.slider("Depth", 1, int(1/.01), 10, step=5)

            ext_depth_nom = 'vAMM' if market_type == 'perp' else 'Openbook'
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                subplot_titles=[ext_depth_nom+' depth', 'DLOB depth'])

            fig.add_trace( go.Scatter(x=drift_depth.index, y=drift_depth['bids'],  name='bids', fill='tozeroy'),  row=1, col=1)
            fig.add_trace( go.Scatter(x=drift_depth.index, y=drift_depth['asks'],  name='asks', fill='tozeroy'),  row=1, col=1)

            fig.add_trace( go.Scatter(x=drift_order_depth.index, y=drift_order_depth['bids'], name='bids',  fill='tozeroy'),  row=2, col=1)
            fig.add_trace( go.Scatter(x=drift_order_depth.index, y=drift_order_depth['asks'], name='asks',  fill='tozeroy'), row=2, col=1)

            st.plotly_chart(fig)


        with tabs[2]:
            price_df = cached_get_price_data(market_type, market_index)
            # print(price_df.columns)
            odf = (price_df.set_index('ts'))
            odf['tradePrice'] = (odf['quoteAssetAmountFilled'].astype(float) * 1e3)/ odf['baseAssetAmountFilled'].astype(float)
            odf['oraclePrice'] = odf['oraclePrice'].astype(float)/1e6
            odf['baseAssetAmountFilled'] = odf['baseAssetAmountFilled'].astype(float)/1e9
            can_cols = ['oraclePrice', 'tradePrice', 'baseAssetAmountFilled', 'taker', 'maker', 'actionExplanation', 'takerOrderDirection']
            can_cols = [x for x in can_cols if x in odf.columns]
            odf = odf[can_cols]
            odf.index = pd.to_datetime([int(x) for x in odf.index.astype(int) * 1e9])
            layout = go.Layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                grid=None,
            xaxis_showgrid=False, yaxis_showgrid=False
            )

            fig = odf[['tradePrice', 'oraclePrice']].plot()
            fig = fig.update_layout(layout)

            col1, col3 = st.columns(2, gap='large')
            col1.plotly_chart(fig, use_container_width=True)

            can_cols2 = [x for x in ['tradePrice', 'baseAssetAmountFilled', 'taker', 'maker', 'actionExplanation', 'takerOrderDirection'] if x in can_cols]
            tbl = odf.reset_index(drop=True)[can_cols2].fillna('vAMM')

            renom_cols = ['Price', 'Size', 'Taker', 'Maker', 'ActionExplanation', 'takerOrderDirection']
            if len(can_cols2) == len(renom_cols):
                tbl.columns = renom_cols
            else:
                tbl.columns = ['Price', 'Size', 'Taker', 'ActionExplanation', 'takerOrderDirection']


            def highlight_survived(s):
                return ['background-color: lightgreen']*len(s) if s.takerOrderDirection=='long' else ['background-color: pink']*len(s)

            def color_survived(val):
                color = 'green' if val else 'red'
                return f'background-color: {color}'

            col3.dataframe(tbl.style.apply(highlight_survived, axis=1), use_container_width=True)

