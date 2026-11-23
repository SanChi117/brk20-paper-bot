"""Общая обвязка бумажных ботов слотов 3–4: состояние, журнал, сделки, команды из веб-терминала.

Формат state совместим с терминалом (brk20_web.py): eq, positions, pendings, trades, paused, risk, started.
Сделка: sym, side, entry, exit, r, r_usd, out, id, entry_t, exit_t, ctx.
"""
import csv
import json
import os
import time
from datetime import datetime, timezone

H = 3600 * 1000


class Slot:
    def __init__(self, tag, name, directory, eq0=1000.0, risk=0.01, engine='1.0'):
        self.tag, self.name, self.dir = tag, name, directory
        self.eq0, self.risk0, self.engine = eq0, risk, engine
        self.set_dir(directory)

    def set_dir(self, d):
        self.dir = d
        j = lambda f: os.path.join(d, f)
        self.STATE_F = j(f'state_{self.tag}.json')
        self.TRADES_F = j(f'trades_{self.tag}.csv')
        self.STATUS_F = j(f'status_{self.tag}.txt')
        self.JOURNAL_F = j(f'journal_{self.tag}.log')
        self.CONTROL_F = j(f'control_{self.tag}.json')

    # ---------- журнал ----------
    def log(self, msg):
        line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC | {msg}"
        print(line, flush=True)
        with open(self.JOURNAL_F, 'a') as f:
            f.write(line + '\n')

    # ---------- состояние ----------
    def default_state(self):
        return {'eq': self.eq0, 'positions': [], 'pendings': [], 'trades': [],
                'started': int(time.time() * 1000), 'paused': False, 'risk': self.risk0,
                'trade_seq': 0, 'engine': self.engine}

    def load_state(self):
        if os.path.exists(self.STATE_F):
            try:
                with open(self.STATE_F) as f:
                    st = json.load(f)
                if st.get('engine') == self.engine:
                    return st
                self._archive(f"логика {st.get('engine')} -> {self.engine}")
            except Exception as e:
                self._archive(f'state не читается ({e})')
        return self.default_state()

    def _archive(self, reason):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        dst = os.path.join(self.dir, 'archive', f'{self.tag}_{stamp}')
        os.makedirs(dst, exist_ok=True)
        for f in (self.STATE_F, self.TRADES_F, self.STATUS_F, self.JOURNAL_F):
            if os.path.exists(f):
                os.replace(f, os.path.join(dst, os.path.basename(f)))
        self.log(f'ГЕЙТ ПЕРЕЗАПУЩЕН: {reason}. Старые файлы в {dst}')

    def save_state(self, st):
        tmp = self.STATE_F + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(st, f)
        os.replace(tmp, self.STATE_F)

    # ---------- сделки ----------
    def book_trade(self, st, pos, px, t, out, pnl, extra=None):
        st['eq'] += pnl
        st['trade_seq'] = st.get('trade_seq', 0) + 1
        tr = {'sym': pos['sym'], 'side': pos['side'], 'entry': pos['entry'], 'exit': px,
              'r': pnl / pos['r_usd'] if pos['r_usd'] else 0.0, 'r_usd': pnl, 'out': out,
              'id': st['trade_seq'], 'entry_t': pos.get('entry_t'), 'exit_t': t,
              'ctx': pos.get('ctx', {})}
        if extra:
            tr.update(extra)
        st['trades'].append(tr)
        new = not os.path.exists(self.TRADES_F)
        with open(self.TRADES_F, 'a', newline='') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['close_time_utc', 'id', 'sym', 'side', 'entry', 'exit', 'r', 'r_usd', 'out'])
            w.writerow([datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'), tr['id'], tr['sym'], tr['side'],
                        f"{tr['entry']:.8g}", f"{px:.8g}", f"{tr['r']:.3f}", f"{pnl:.2f}", out])
        if pos in st['positions']:
            st['positions'].remove(pos)
        self.log(f"EXIT {pos['sym']} {pos['side']} {out} @ {px:.6g} | R {tr['r']:+.2f} | {pnl:+.2f}$ | eq {st['eq']:.2f}")
        return tr

    @staticmethod
    def stats(st):
        trs = [t for t in st['trades'] if t.get('out') != 'manual']
        gw = sum(t['r_usd'] for t in trs if t['r_usd'] > 0)
        gl = -sum(t['r_usd'] for t in trs if t['r_usd'] < 0)
        pf = gw / gl if gl > 0 else float('inf')
        wr = sum(1 for t in trs if t['r_usd'] > 0) / max(1, len(trs))
        return len(trs), pf, wr

    def write_status(self, st, extra=''):
        n, pf, wr = self.stats(st)
        gate = 'ИДЁТ' if n < 30 else ('ПРОЙДЕН' if pf > 1.2 else 'ПРОВАЛЕН')
        txt = (f"{self.name} PAPER | equity {st['eq']:.2f}$ | сделок {n}/30 | PF {pf:.2f} | WR {wr * 100:.0f}% | "
               f"риск {st.get('risk', self.risk0) * 100:.2f}% | позиций {len(st['positions'])} | гейт: {gate}"
               f"{' | ПАУЗА' if st.get('paused') else ''}{extra} | {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n")
        with open(self.STATUS_F, 'w') as f:
            f.write(txt)

    # ---------- команды из терминала ----------
    def apply_controls(self, st, close_fn):
        """close_fn(st, pos, note) закрывает позицию по текущей цене (реализует бот)."""
        if not os.path.exists(self.CONTROL_F):
            return
        try:
            with open(self.CONTROL_F) as f:
                cmds = json.load(f)
            os.remove(self.CONTROL_F)
        except Exception:
            return
        allow = os.environ.get('BRK20_ALLOW_GATE_OVERRIDES') == '1'
        for c in cmds:
            cmd, sym = c.get('cmd'), c.get('sym')
            if cmd == 'pause':
                st['paused'] = True; self.log('WEB: пауза (новые входы заморожены, открытые позиции ведутся)')
            elif cmd == 'resume':
                st['paused'] = False; self.log('WEB: пауза снята')
            elif cmd in ('cancel', 'cancel_all'):
                n0 = len(st['pendings'])
                st['pendings'] = [p for p in st['pendings'] if cmd == 'cancel' and p['sym'] != sym]
                if len(st['pendings']) < n0:
                    self.log(f'WEB: отменено заявок: {n0 - len(st["pendings"])}')
            elif cmd == 'close' and sym:
                for pos in [p for p in st['positions'] if p['sym'] == sym]:
                    close_fn(st, pos, c.get('note'))
            elif cmd == 'close_all':
                for pos in list(st['positions']):
                    close_fn(st, pos, c.get('note'))
                self.log('WEB: закрыть все — выполнено')
            elif cmd in ('set_risk', 'set_equity') and not allow:
                self.log(f'WEB: отказ в {cmd} — во время форвард-гейта риск и equity не меняются '
                         f'(BRK20_ALLOW_GATE_OVERRIDES=1 чтобы разрешить)')
            elif cmd == 'set_risk':
                st['risk'] = max(0.001, min(0.2, float(c.get('value', self.risk0))))
                self.log(f"WEB: риск на сделку {st['risk'] * 100:.2f}%")
            elif cmd == 'set_equity':
                if st['positions']:
                    self.log('WEB: отказ в set_equity — есть открытые позиции')
                else:
                    st['eq'] = float(c.get('value', st['eq'])); self.log(f"WEB: equity {st['eq']:.2f}")
