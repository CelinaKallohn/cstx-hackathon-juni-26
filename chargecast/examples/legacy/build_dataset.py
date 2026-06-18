import pandas as pd, numpy as np

UP = '/mnt/user-data/uploads/'

def load_demand():
    df = pd.read_excel(UP+'Lastgang_Ladeinfrastruktur_Beispiel_Ladehub.xlsx', sheet_name='2025', header=1)
    df = df[['Ab-Datum','Ab-Zeit','Profilwert\nkWh']].dropna(subset=['Ab-Zeit'])
    df.columns = ['date','time','kwh']
    df['kwh'] = pd.to_numeric(df['kwh'], errors='coerce')
    df['dt'] = pd.to_datetime(df['date'].astype(str)+' '+df['time'].astype(str))
    df['hourstamp'] = df['dt'].dt.floor('h')
    h = df.groupby('hourstamp')['kwh'].sum().reset_index()
    return h

def load_spot():
    sp = pd.read_excel(UP+'Spotmarktpreis_.xlsx')
    sp.columns = ['d','von','tz1','bis','tz2','price']
    def hr(t):
        return t.hour if hasattr(t,'hour') else int(round(float(t)*24))%24
    sp['hour'] = sp['von'].apply(hr)
    sp['date'] = pd.to_datetime(sp['d'])
    sp['hourstamp'] = sp['date'] + pd.to_timedelta(sp['hour'], unit='h')
    s = sp.groupby('hourstamp')['price'].mean().reset_index()
    return s

def build():
    h = load_demand()
    s = load_spot()
    df = h.merge(s, on='hourstamp', how='left')
    df['spot_ct'] = df['price'].fillna(df['price'].median())
    df = df.drop(columns=['price'])
    ts = df['hourstamp']
    df['hour'] = ts.dt.hour
    df['dayofweek'] = ts.dt.dayofweek
    df['month'] = ts.dt.month
    df['is_weekend'] = (ts.dt.dayofweek >= 5).astype(int)
    df['dayofyear'] = ts.dt.dayofyear
    df['trend'] = (ts - ts.min()).dt.total_seconds() / 3600.0
    df['hour_sin'] = np.sin(2*np.pi*df['hour']/24)
    df['hour_cos'] = np.cos(2*np.pi*df['hour']/24)
    df['target_kwh'] = df['kwh']
    df = df.drop(columns=['kwh'])
    return df

if __name__ == '__main__':
    df = build()
    df.to_csv('/home/claude/charging_hourly_dataset.csv', index=False)
    print('rows:', len(df))
    print('cols:', list(df.columns))
    print(df[['hourstamp','hour','dayofweek','month','is_weekend','spot_ct','target_kwh']].head().to_string())
    print('target mean/std:', round(df.target_kwh.mean(),2), round(df.target_kwh.std(),2))
