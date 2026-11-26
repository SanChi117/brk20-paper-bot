import glob, os, sys, csv
import numpy as np
for dd in sys.argv[1:3]:
    fs = sorted(glob.glob(f'{dd}/*_funding.csv'))
    print(dd, 'funding files', len(fs))
    for p in fs[:2]:
        rows = list(csv.DictReader(open(p)))
        print(os.path.basename(p), rows[:3], len(rows))
    for s in ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'SEIUSDT'):
        p = f'{dd}/{s}_funding.csv'
        if os.path.exists(p):
            r = np.array([float(x['rate']) for x in csv.DictReader(open(p))])
            t = np.array([int(x['t']) for x in csv.DictReader(open(p))])
            yrs = (t[-1] - t[0]) / 3.15576e10
            print(s, 'n', len(r), 'mean/8h %.5f%%' % (r.mean() * 100), 'APR if always short perp: %.1f%%' % (r.sum() / yrs * 100), 'share>0.01%%: %.2f' % (r > 0.0001).mean(), 'dt median h', np.median(np.diff(t)) / 3.6e6)
