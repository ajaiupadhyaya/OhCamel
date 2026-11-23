//! The engine's accounting loop (contract II.4 `backtest_weights`): the inner
//! loop of backtest/engine.run_weights, verbatim. For session s with returns R_s
//! and post-trade weights w carried from s - 1:
//!   gross_s     = sum(w R_s) + (1 - sum w) rf_s
//!   borrow_s    = b/1e4/252 x (sum|w| - sum w)/2
//!   financing_s = f/1e4/252 x max(sum|w| - 1, 0)    (completion task 2.3)
//!   g_s = gross_s - borrow_s - financing_s;  w <- (w + w R_s) / (1 + g_s)   (drift)
//! and at an execution session with target w*, with c_i name i's half-spread
//! (bps, one per name; research/config/costs.yaml's tier in the engine):
//!   turnover_s = sum|w* - w|;  cost_s = sum_i c_i/1e4 x |w*_i - w_i|;
//!   1 + net_s = (1 + g_s)(1 - cost_s);  w <- w*.
//! Before the first execution the book is cash and net = gross = rf. A session
//! with 1 + g_s <= 0 wipes the book out: net = -1 and the loop stops (later rows
//! stay zero). `exec_idx[j]` is the session at whose close target row j is
//! executed -- run_weights adds the execution lag before calling.
pub struct Path {
    pub gross: Vec<f64>,
    pub net: Vec<f64>,
    pub weights: Vec<f64>,
    pub turnover: Vec<f64>,
    pub trades: Vec<f64>,
    pub costs: Vec<f64>,
    pub borrow: Vec<f64>,
    pub financing: Vec<f64>,
    pub ruined: bool,
}

pub fn backtest_weights(
    px: &[f64],
    nt: usize,
    na: usize,
    target: &[f64],
    exec_idx: &[i64],
    cost_bps: &[f64],
    borrow_bps: f64,
    fin_bps: f64,
    rf: &[f64],
) -> Result<Path, String> {
    if nt < 2 || na == 0 || px.len() != nt * na {
        return Err(format!(
            "prices must be n_t x n_a with n_t >= 2; got {} values for {nt} x {na}",
            px.len()
        ));
    }
    if px.iter().any(|&p| !(p.is_finite() && p > 0.0)) {
        return Err("prices must be finite and positive".into());
    }
    if target.len() != exec_idx.len() * na || target.iter().any(|v| !v.is_finite()) {
        return Err("target_w must be finite, one row of n_a weights per execution".into());
    }
    if rf.len() != nt || rf.iter().any(|v| !v.is_finite()) {
        return Err(format!("rf must be {nt} finite values"));
    }
    if cost_bps.len() != na {
        return Err(format!(
            "cost_bps must hold one cost per name: {} for {na}",
            cost_bps.len()
        ));
    }
    if cost_bps.iter().any(|c| !(c.is_finite() && *c >= 0.0))
        || !(borrow_bps.is_finite() && borrow_bps >= 0.0 && fin_bps.is_finite() && fin_bps >= 0.0)
    {
        return Err("costs must be finite and non-negative".into());
    }
    for (j, &s) in exec_idx.iter().enumerate() {
        if s < 1 || s as usize >= nt || (j > 0 && s <= exec_idx[j - 1]) {
            return Err("decision_idx must be strictly increasing sessions in 1..n_t (an execution needs a prior close)".into());
        }
    }
    let mut p = Path {
        gross: vec![0.0; nt],
        net: vec![0.0; nt],
        weights: vec![0.0; nt * na],
        turnover: vec![0.0; nt],
        trades: vec![0.0; nt * na],
        costs: vec![0.0; nt],
        borrow: vec![0.0; nt],
        financing: vec![0.0; nt],
        ruined: false,
    };
    let first = exec_idx.first().map(|&s| s as usize).unwrap_or(nt);
    p.gross[1..first].copy_from_slice(&rf[1..first]);
    p.net[1..first].copy_from_slice(&rf[1..first]);
    let c: Vec<f64> = cost_bps.iter().map(|x| x / 1e4).collect();
    let b = borrow_bps / 1e4 / 252.0;
    let f = fin_bps / 1e4 / 252.0;
    let mut w = vec![0.0; na];
    let mut wr = vec![0.0; na];
    let mut e = 0usize;
    for s in first..nt {
        let r = |i: usize| px[s * na + i] / px[(s - 1) * na + i] - 1.0;
        let (mut sum_wr, mut wsum, mut wabs) = (0.0, 0.0, 0.0);
        for i in 0..na {
            wr[i] = w[i] * r(i);
            sum_wr += wr[i];
            wsum += w[i];
            wabs += w[i].abs();
        }
        let gs = sum_wr + (1.0 - wsum) * rf[s];
        let bs = if b != 0.0 {
            b * 0.5 * (wabs - wsum)
        } else {
            0.0
        };
        let fs = if f != 0.0 && wabs > 1.0 {
            f * (wabs - 1.0)
        } else {
            0.0
        };
        p.gross[s] = gs;
        p.borrow[s] = bs;
        p.financing[s] = fs;
        let g = gs - bs - fs;
        if 1.0 + g <= 0.0 {
            p.net[s] = -1.0;
            p.ruined = true;
            break;
        }
        for i in 0..na {
            w[i] = (w[i] + wr[i]) / (1.0 + g);
        }
        p.net[s] = g;
        if e < exec_idx.len() && exec_idx[e] as usize == s {
            let tw = &target[e * na..(e + 1) * na];
            let (mut to, mut cost) = (0.0, 0.0);
            for i in 0..na {
                let dw = tw[i] - w[i];
                p.trades[s * na + i] = dw;
                to += dw.abs();
                cost += c[i] * dw.abs();
            }
            p.turnover[s] = to;
            p.costs[s] = cost;
            p.net[s] = (1.0 + p.net[s]) * (1.0 - p.costs[s]) - 1.0;
            w.copy_from_slice(tw);
            e += 1;
        }
        p.weights[s * na..(s + 1) * na].copy_from_slice(&w);
    }
    Ok(p)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn long_one_asset_hand() {
        // prices 100, 110, 99; buy w = 1 at the close of session 1, 10 bps, rf 0.
        // s = 1: cash, g = 0; execution: turnover 1, cost 0.001, net = (1)(1 - 0.001) - 1 = -0.001
        // s = 2: R = 99/110 - 1 = -0.1, g = -0.1, w = (1 - 0.1)/0.9 = 1, net = -0.1
        let p = backtest_weights(
            &[100.0, 110.0, 99.0],
            3,
            1,
            &[1.0],
            &[1],
            &[10.0],
            0.0,
            0.0,
            &[0.0; 3],
        )
        .unwrap();
        assert_eq!(p.turnover, vec![0.0, 1.0, 0.0]);
        assert!((p.net[1] + 0.001).abs() < 1e-15 && (p.net[2] + 0.1).abs() < 1e-15);
        assert_eq!(p.weights, vec![0.0, 1.0, 1.0]);
        assert!(!p.ruined);
    }

    #[test]
    fn short_pays_borrow_hand() {
        // short w = -1 at session 1, 252 bps/yr borrow -> b = 0.0252/252 = 1e-4 a day.
        // s = 2: w R = -1 x -0.1 = 0.1; gross = 0.1 + (1 - (-1)) x 0 = 0.1;
        // borrow = 1e-4 x (|-1| - (-1))/2 = 1e-4; g = 0.0999; w = (-1 + 0.1)/1.0999
        let p = backtest_weights(
            &[100.0, 110.0, 99.0],
            3,
            1,
            &[-1.0],
            &[1],
            &[0.0],
            252.0,
            0.0,
            &[0.0; 3],
        )
        .unwrap();
        assert!((p.borrow[2] - 1e-4).abs() < 1e-18);
        assert!((p.net[2] - 0.0999).abs() < 1e-15);
        assert!((p.weights[2] - (-0.9 / 1.0999)).abs() < 1e-15);
    }

    #[test]
    fn cash_earns_rf_before_the_first_execution() {
        let p = backtest_weights(
            &[100.0, 100.0, 100.0, 100.0],
            4,
            1,
            &[1.0],
            &[3],
            &[0.0],
            0.0,
            0.0,
            &[0.0, 0.01, 0.02, 0.03],
        )
        .unwrap();
        assert_eq!(&p.net[..3], &[0.0, 0.01, 0.02]);
        assert_eq!(&p.gross[..3], &[0.0, 0.01, 0.02]);
    }

    #[test]
    fn turnover_is_against_the_drifted_weights() {
        // 0.5 long executed at session 1 and again at session 2, 10 bps.
        // s = 2: r = 0.1, g = 0.5 x 0.1 = 0.05, so w drifts from 0.5 to 0.55 / 1.05 before
        // the execution; the trade back to 0.5 is 0.5 - 0.55 / 1.05 = -0.025 / 1.05, so
        // turnover = 0.025 / 1.05 = 0.0238095... (zero if measured against the pre-drift 0.5).
        let p = backtest_weights(
            &[100.0, 100.0, 110.0],
            3,
            1,
            &[0.5, 0.5],
            &[1, 2],
            &[10.0],
            0.0,
            0.0,
            &[0.0; 3],
        )
        .unwrap();
        assert!(
            (p.turnover[2] - 0.025 / 1.05).abs() < 1e-15,
            "{}",
            p.turnover[2]
        );
        assert!((p.trades[2] + 0.025 / 1.05).abs() < 1e-15);
        assert!((p.costs[2] - 1e-3 * 0.025 / 1.05).abs() < 1e-18);
    }

    #[test]
    fn borrow_is_charged_on_short_notional_only() {
        // 1.0 long A, 0.5 short B, flat prices, 252 bps/yr -> b = 1e-4 a day.
        // short notional = (|w| sum - w sum) / 2 = (1.5 - 0.5) / 2 = 0.5, so borrow = 0.5e-4
        // (charging gross notional 1.5 would give 1.5e-4); gross = 0, net = -0.5e-4.
        let p = backtest_weights(
            &[100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            3,
            2,
            &[1.0, -0.5],
            &[1],
            &[0.0; 2],
            252.0,
            0.0,
            &[0.0; 3],
        )
        .unwrap();
        assert!((p.borrow[2] - 0.5e-4).abs() < 1e-18, "{}", p.borrow[2]);
        assert!((p.net[2] + 0.5e-4).abs() < 1e-18);
    }

    #[test]
    fn ruin_stops_the_book() {
        // 2x long, then a -60 % day: g = 2 x -0.6 = -1.2, 1 + g <= 0 -> net = -1, weights zero after
        let p = backtest_weights(
            &[100.0, 100.0, 40.0, 50.0],
            4,
            1,
            &[2.0],
            &[1],
            &[0.0],
            0.0,
            0.0,
            &[0.0; 4],
        )
        .unwrap();
        assert!(p.ruined && p.net[2] == -1.0);
        assert_eq!(&p.weights[2..], &[0.0, 0.0]);
        assert_eq!(p.net[3], 0.0);
    }

    #[test]
    fn per_asset_cost_and_financing_hand() {
        // Completion task 2.3. Two names at 100, rf 0; buy 1.0 of each at session 1 (gross 2)
        // with half-spreads 1 and 8 bp: cost = 1e-4 + 8e-4 = 9e-4, net[1] = -9e-4.
        // s = 2, no move: financing 50 bp/yr on the one unit above 1, f = 50e-4/252;
        // g = -f, net[2] = -f, and each weight drifts to 1/(1 - f) as the fee shrinks NAV.
        let p = backtest_weights(
            &[100.0; 6],
            3,
            2,
            &[1.0, 1.0],
            &[1],
            &[1.0, 8.0],
            0.0,
            50.0,
            &[0.0; 3],
        )
        .unwrap();
        let f = 50e-4 / 252.0;
        assert!((p.costs[1] - 9e-4).abs() < 1e-18, "{}", p.costs[1]);
        assert!((p.net[1] + 9e-4).abs() < 1e-15);
        assert!((p.financing[2] - f).abs() < 1e-18, "{}", p.financing[2]);
        assert!(p.financing[1] == 0.0 && p.borrow[2] == 0.0);
        assert!((p.net[2] + f).abs() < 1e-15);
        assert!((p.weights[4] - 1.0 / (1.0 - f)).abs() < 1e-15);
    }

    #[test]
    fn gross_at_or_below_one_pays_no_financing() {
        // 0.6 long A, 0.4 short B: gross 1.0, nothing above 1 to finance (borrow still due on B).
        let p = backtest_weights(
            &[100.0; 6],
            3,
            2,
            &[0.6, -0.4],
            &[1],
            &[0.0; 2],
            252.0,
            50.0,
            &[0.0; 3],
        )
        .unwrap();
        assert_eq!(p.financing, vec![0.0; 3]);
        assert!((p.borrow[2] - 0.4e-4).abs() < 1e-18);
    }

    #[test]
    fn rejects_bad_input() {
        let px = [100.0, 101.0, 102.0];
        assert!(backtest_weights(&px, 3, 1, &[1.0], &[0], &[0.0], 0.0, 0.0, &[0.0; 3]).is_err()); // execution needs a prior close
        assert!(backtest_weights(&px, 3, 1, &[1.0], &[3], &[0.0], 0.0, 0.0, &[0.0; 3]).is_err()); // past the end
        assert!(
            backtest_weights(&px, 3, 1, &[1.0, 1.0], &[2, 1], &[0.0], 0.0, 0.0, &[0.0; 3]).is_err()
        ); // not increasing
        assert!(backtest_weights(
            &[100.0, -1.0, 2.0],
            3,
            1,
            &[1.0],
            &[1],
            &[0.0],
            0.0,
            0.0,
            &[0.0; 3]
        )
        .is_err());
        assert!(backtest_weights(&px, 3, 1, &[1.0], &[1], &[-1.0], 0.0, 0.0, &[0.0; 3]).is_err());
        assert!(
            backtest_weights(&px, 3, 1, &[1.0], &[1], &[0.0, 0.0], 0.0, 0.0, &[0.0; 3]).is_err()
        ); // one cost per name
        assert!(backtest_weights(&px, 3, 1, &[1.0], &[1], &[0.0], 0.0, -1.0, &[0.0; 3]).is_err());
        // negative financing
    }
}
