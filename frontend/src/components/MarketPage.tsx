// Desk v8 (owner order 2026-08-22): the FULL MARKET PAGE, structured
// like the real venue apps' market screens — sticky header (back
// chevron, title, close time), big price chart with 1H/24H/7D range
// toggles on top, outcome rows with venue-styled buy buttons, YOUR
// POSITION card with CASH OUT when the account holds this market, and
// the desk's blotter rows for this market. Presentation only: outcome
// taps call TradeDesk's choose() (which opens the existing order
// panel), CASH OUT calls TradeDesk's openCashOut() (the existing
// modal) — every money path stays in TradeDesk untouched.

import { useMemo, useState } from 'react'
import {
  PriceChart, useDeskHistory, type HistoryVenue,
} from './PriceChart'
import type {
  CoTarget, GameView, ManualTrade, Pick as DeskPick, Venue,
} from '../pages/TradeDesk'

export interface MarketMeta {
  close_time: string | null
  volume_usd: number | null
  history_id: string | null
}

const cents = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}¢`)
const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`
const signed = (v: number | null | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`

export function MarketPage({
  venue, game, meta, loading, pick, choose, onBack,
  positions, openCashOut, coBusy, trades,
}: {
  venue: Venue
  game: GameView | null           // null while loading (or on failure)
  meta: MarketMeta | null
  loading: boolean
  pick: DeskPick | null
  choose: (p: DeskPick) => void
  onBack: () => void
  positions: CoTarget[]           // the venue's account positions (all)
  openCashOut: (t: CoTarget) => void
  coBusy: boolean
  trades: ManualTrade[]           // full desk blotter — filtered here
}) {
  const isK = venue === 'kalshi'
  const [hours, setHours] = useState<1 | 24 | 168>(24)

  // ── Chart id: the active pick when it belongs to this market, else
  // the feed card's history_id, else the first chartable outcome. ────
  const pickId = pick
    ? (pick.venue === 'polymarket' ? pick.asset : pick.ticker)
    : undefined
  const pickOnGame = !!(pick && game && pick.label === game.title && pickId)
  const fallbackId = useMemo(() => {
    if (meta?.history_id) return meta.history_id
    for (const grp of game?.groups || []) {
      for (const mk of grp.markets) {
        const id = isK ? mk.ticker : mk.asset
        if (id) return id
      }
    }
    return undefined
  }, [game, meta, isK])
  const chartId = pickOnGame ? pickId : fallbackId
  const hv: HistoryVenue | undefined =
    chartId ? (isK ? 'kalshi' : 'polymarket-us') : undefined
  const points = useDeskHistory(hv, chartId, hours)
  const last = points && points.length ? points[points.length - 1].p : null
  const first = points && points.length ? points[0].p : null
  const delta = last != null && first != null
    ? Math.round((last - first) * 100) : null

  // ── YOUR POSITION: account snapshot rows that live on this market ──
  const held = useMemo(() => {
    if (!game) return []
    const ids = new Set<string>()
    for (const grp of game.groups) {
      for (const mk of grp.markets) {
        if (mk.ticker) ids.add(mk.ticker)
        if (mk.us_slug) ids.add(mk.us_slug)
      }
    }
    return positions.filter((t) => isK
      ? !!t.ticker && (ids.has(t.ticker) || t.ticker.startsWith(`${game.id}-`))
      : !!t.usSlug && ids.has(t.usSlug))
  }, [game, positions, isK])

  // ── Desk blotter rows for this market (title-keyed best effort) ────
  const myTrades = useMemo(() => {
    if (!game) return []
    const t0 = game.title.toLowerCase()
    return trades
      .filter((t) => t0 && (t.title || '').toLowerCase().includes(t0))
      .slice(0, 12)
  }, [game, trades])

  const closeTxt = useMemo(() => {
    if (!meta?.close_time) return null
    const d = new Date(meta.close_time)
    if (Number.isNaN(d.getTime())) return null
    const sameDay = d.toDateString() === new Date().toDateString()
    return `Closes ${sameDay
      ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
      : d.toLocaleDateString([], { month: 'short', day: 'numeric' })}`
  }, [meta])
  const volTxt = meta?.volume_usd == null ? null
    : meta.volume_usd >= 1e6 ? `$${(meta.volume_usd / 1e6).toFixed(1)}M Vol`
      : meta.volume_usd >= 1e3 ? `$${Math.round(meta.volume_usd / 1e3)}K Vol`
        : `$${Math.round(meta.volume_usd)} Vol`

  return (
    <section className={`mp ${isK ? 'kx8' : 'pm8'}`}>
      <div className="mp-head">
        <button className="mp-back" onClick={onBack} aria-label="Back to markets">‹</button>
        <span className="mp-ico" aria-hidden>
          {isK ? '📊' : '📈'}
        </span>
        <div className="mp-title">
          {game?.title || (loading ? 'Loading market…' : 'Market')}
          <small>
            {[closeTxt, volTxt].filter(Boolean).join(' · ')
              || (isK ? 'Kalshi' : 'Polymarket')}
          </small>
        </div>
        {last != null && (
          <span className="mp-last">
            {Math.round(last * 100)}¢
            {delta != null && delta !== 0 && (
              <small className={delta > 0 ? 'pos' : 'neg'}>
                {delta > 0 ? '▲' : '▼'}{Math.abs(delta)}¢
              </small>
            )}
          </span>
        )}
      </div>

      <div className="mp-chart">
        <div className="mp-ranges" role="group" aria-label="History range">
          {([[1, '1H'], [24, '24H'], [168, '7D']] as const).map(([h, l]) => (
            <button
              key={h} className={hours === h ? 'on' : ''}
              onClick={() => setHours(h)}
            >{l}</button>
          ))}
        </div>
        {chartId
          ? <PriceChart points={points} hours={hours} />
          : <div className="pc-note">No chartable outcome for this market yet.</div>}
        {pickOnGame && pick && (
          <div className="mp-chart-sub">
            {pick.side}
            {isK && pick.kalshiSide === 'no' ? ' · market yes price' : ''}
            {' — price history'}
          </div>
        )}
      </div>

      {held.map((t) => (
        <div className="mp-pos" key={`${t.usSlug || t.ticker}-${t.outcome || ''}`}>
          <div className="mp-pos-h">Your position<span>{t.outcome || t.ticker}</span></div>
          <div className="mp-pos-grid">
            <span>Qty<b>{Math.round(t.held)}</b></span>
            <span>Cost<b>{money(t.cost)}</b></span>
            <span>Mark<b>{cents(t.mark)}</b></span>
            <span>Value<b>{money(t.value)}</b></span>
            <span>Unrealized<b className={(t.unrealized ?? 0) > 0 ? 'pos' : (t.unrealized ?? 0) < 0 ? 'neg' : ''}>
              {signed(t.unrealized)}
            </b></span>
          </div>
          <button className="dx-cashout" disabled={coBusy} onClick={() => openCashOut(t)}>
            CASH OUT
          </button>
        </div>
      ))}

      {!game ? (
        loading ? (
          <div className="dx-skel-rows bare" aria-label="Loading market">
            <div className="tr-skel" /><div className="tr-skel" /><div className="tr-skel" />
          </div>
        ) : (
          <p className="vd-empty">Market failed to load — go back and retry.</p>
        )
      ) : (
        <>
          {game.positions.length > 0 && (
            <div className="dx-pos">
              <div className="dx-pos-h">Engine positions (held to resolution)</div>
              {game.positions.map((p) => (
                <div className="dx-pos-row" key={p.asset}>
                  <b>{p.outcome || 'position'}</b>
                  <span>Cost {money(p.cost)} @ {cents(p.fill_price)}</span>
                  <span>To win <b>{money(p.to_win)}</b></span>
                  {p.pnl != null && (
                    <span className={p.pnl >= 0 ? 'pos' : 'neg'}>settled {money(p.pnl)}</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {game.groups.map((grp) => (
            <div className="dx-group" key={grp.name}>
              <div className="dx-group-h">{grp.name}<span className="dx-group-n">{grp.markets.length}</span></div>
              {grp.markets.map((mk) => (
                <div className="dx-group-row" key={mk.us_slug || mk.asset || mk.ticker || mk.label}>
                  <span className="dx-group-label">{mk.label}</span>
                  {isK ? (
                    <>
                      <button
                        className={`dx-mini yes${pick?.ticker === mk.ticker && pick?.kalshiSide !== 'no' ? ' on' : ''}`}
                        disabled={mk.price == null}
                        onClick={() => mk.price != null && choose({
                          venue: 'kalshi', label: game.title, side: mk.label || 'YES',
                          ask: mk.price, ticker: mk.ticker, kalshiSide: 'yes',
                        })}
                      >Yes {cents(mk.price)}</button>
                      <button
                        className={`dx-mini no${pick?.ticker === mk.ticker && pick?.kalshiSide === 'no' ? ' on' : ''}`}
                        disabled={mk.no_price == null}
                        onClick={() => mk.no_price != null && choose({
                          venue: 'kalshi', label: game.title, side: `NO ${mk.label}`,
                          ask: mk.no_price!, ticker: mk.ticker, kalshiSide: 'no',
                        })}
                      >No {cents(mk.no_price)}</button>
                    </>
                  ) : (
                    <button
                      className={`dx-mini yes${(mk.asset && pick?.asset === mk.asset)
                        || (mk.us_slug && pick?.usSlug === mk.us_slug) ? ' on' : ''}`}
                      disabled={mk.price == null && !mk.us_slug}
                      onClick={() => (mk.price != null || mk.us_slug) && choose({
                        venue: 'polymarket', label: game.title, side: mk.label || 'Yes',
                        ask: mk.price ?? 0, asset: mk.asset, usSlug: mk.us_slug,
                      })}
                    >Buy{mk.price != null ? ` ${cents(mk.price)}` : ''}</button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </>
      )}

      {myTrades.length > 0 && (
        <div className="mp-trades">
          <div className="mp-trades-h">Desk orders — this market</div>
          {myTrades.map((t) => (
            <div className="mp-trade" key={t.id}>
              <span className="mp-trade-t">
                {t.placed_at ? new Date(t.placed_at).toLocaleTimeString() : '—'}
              </span>
              <span className="mp-trade-o">{t.outcome || '—'}</span>
              <span className={`dx-blot-st ${t.status}`}>{t.status}</span>
              <span className="mp-trade-n">{money(t.filled_usd || t.requested_usd)}</span>
              <b className={(t.pnl ?? 0) > 0 ? 'pos' : (t.pnl ?? 0) < 0 ? 'neg' : ''}>
                {money(t.pnl)}
              </b>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
