import csv, collections, statistics, sys
r = list(csv.DictReader(open(sys.argv[1])))
print('by year', sorted(collections.Counter(x['date'][:4] for x in r).items()))
print('by month 2026', sorted(collections.Counter(x['date'][:7] for x in r if x['date'] >= '2025-10').items()))
h = [x for x in r if x['holdout'] == '1']
print('holdout sample', [(x['sym'], x['date'], int(float(x['qv0']))) for x in h[::5]])
for y in ('2023', '2024', '2025', '2026'):
    v = [float(x['qv0']) for x in r if x['date'][:4] == y]
    print(y, 'median qv0', int(statistics.median(v)), 'new', sum(x['new'] == '1' for x in r if x['date'][:4] == y), '/', len(v))
