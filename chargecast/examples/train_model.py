import pandas as pd, numpy as np, pickle, json
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

FEATURES = ['spot_ct','hour','dayofweek','month','is_weekend','dayofyear','trend','hour_sin','hour_cos']

def main():
    df = pd.read_csv('/home/claude/charging_hourly_dataset.csv', parse_dates=['hourstamp'])
    df = df.sort_values('hourstamp').reset_index(drop=True)
    split = int(len(df)*0.8)
    tr, te = df.iloc[:split], df.iloc[split:]
    Xtr, ytr = tr[FEATURES], tr['target_kwh']
    Xte, yte = te[FEATURES], te['target_kwh']

    model = HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.05, max_depth=4,
        l2_regularization=1.0, random_state=42)
    model.fit(Xtr, ytr)

    pred = np.clip(model.predict(Xte), 0, None)
    gbm_mae = mean_absolute_error(yte, pred)
    gbm_r2 = r2_score(yte, pred)

    hour_avg = tr.groupby('hour')['target_kwh'].mean()
    naive = te['hour'].map(hour_avg).values
    naive_mae = mean_absolute_error(yte, naive)
    naive_r2 = r2_score(yte, naive)

    imp = {}
    try:
        from sklearn.inspection import permutation_importance
        r = permutation_importance(model, Xte, yte, n_repeats=5, random_state=42, n_jobs=-1)
        imp = {f: round(float(v),3) for f,v in sorted(zip(FEATURES, r.importances_mean), key=lambda x:-x[1])}
    except Exception as e:
        imp = {'error': str(e)}

    metrics = {
        'n_train': len(tr), 'n_test': len(te),
        'gbm_mae_kwh': round(float(gbm_mae),3), 'gbm_r2': round(float(gbm_r2),4),
        'naive_hour_avg_mae_kwh': round(float(naive_mae),3), 'naive_hour_avg_r2': round(float(naive_r2),4),
        'mae_improvement_pct': round(float((naive_mae-gbm_mae)/naive_mae*100),1),
        'permutation_importance': imp,
    }
    with open('/home/claude/model.pkl','wb') as f:
        pickle.dump({'model':model,'features':FEATURES}, f)
    with open('/home/claude/metrics.json','w') as f:
        json.dump(metrics, f, indent=2)
    print(json.dumps(metrics, indent=2))

if __name__ == '__main__':
    main()
