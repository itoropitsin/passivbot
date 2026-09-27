//! Stand-alone TLI-Wings v8 portfolio-risk hook.
//!
//! This module deliberately does not call the existing orchestrator or risk
//! engine. It evaluates a complete, already prepared portfolio snapshot and
//! returns per-action decisions for a later TLI integration.
//!
//! Ratio fields use decimal fractions: `0.80` means 80 percent. Cash fields
//! are expressed in USDT and end in `_usdt`. The JSON/PyO3 boundary is
//! intentionally explicit so serde remains the single schema definition and
//! malformed input cannot become a neutral value.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::error::Error;
use std::fmt;

pub const TLI_POLICY_SCHEMA_VERSION: &str = "tli-runtime-policy-v1";
pub const TLI_SIZING_BALANCE_CAP_USDT: f64 = 1_000.0;
pub const TLI_MAX_AGGREGATE_EXPOSURE_RATIO: f64 = 0.80;
pub const TLI_MAX_SYMBOL_EXPOSURE_RATIO: f64 = 0.50;
pub const TLI_MAX_USED_MARGIN_RATIO: f64 = 0.35;
pub const TLI_MAX_ACTIVE_GRIDS: u32 = 2;
pub const TLI_MAX_INITIAL_ENTRY_SIZING_BALANCE_RATIO: f64 = 0.03;
pub const TLI_MAX_DAILY_LOSS_RATIO: f64 = 0.02;
pub const TLI_MIN_ENTRY_LIQUIDATION_DISTANCE: f64 = 0.30;
pub const TLI_MIN_REDUCE_ONLY_LIQUIDATION_DISTANCE: f64 = 0.20;
pub const TLI_MIN_EMERGENCY_LIQUIDATION_DISTANCE: f64 = 0.15;
pub const TLI_MAX_LEVERAGE_CAP: f64 = 3.0;

/// The position side is separate from the order direction.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TliPositionSide {
    Long,
    Short,
}

/// A requested action in the snapshot.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TliActionKind {
    InitialEntry,
    GridEntry,
    Close,
    Reduce,
    Panic,
}

impl TliActionKind {
    pub const fn is_entry(self) -> bool {
        matches!(self, Self::InitialEntry | Self::GridEntry)
    }

    pub const fn is_protective(self) -> bool {
        matches!(self, Self::Close | Self::Reduce | Self::Panic)
    }
}

/// Current exposure for one symbol and side.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TliPositionSnapshot {
    pub symbol: String,
    pub side: TliPositionSide,
    /// Gross exposure in USDT, measured against `sizing_balance_cap`.
    pub exposure_usdt: f64,
}

/// One proposed action evaluated against the snapshot.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TliRiskAction {
    pub symbol: String,
    pub side: TliPositionSide,
    pub kind: TliActionKind,
    /// Exposure added by an entry, or the affected exposure for a protective action.
    pub exposure_usdt: f64,
    /// Margin added by an entry, or the affected margin for a protective action.
    pub margin_usdt: f64,
    /// Distance after the proposed action, expressed as a ratio.
    pub liquidation_distance_pct: f64,
    pub leverage: f64,
    /// Set this only when the action creates a new active grid.
    pub opens_grid: bool,
}

/// Complete account snapshot needed by the stand-alone hook.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TliPortfolioSnapshot {
    pub positions: Vec<TliPositionSnapshot>,
    pub used_margin_usdt: f64,
    pub active_grids: u32,
    pub daily_loss_usdt: f64,
    pub actions: Vec<TliRiskAction>,
}

/// TLI v8 policy. Limits may be made stricter, but cannot relax the stated v8
/// safety envelope.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TliRiskPolicy {
    pub schema_version: String,
    pub collateral_currency: String,
    pub sizing_balance_cap: f64,
    pub max_initial_entry_sizing_balance_pct: f64,
    pub max_total_exposure_ratio: f64,
    pub max_symbol_exposure_ratio: f64,
    pub max_used_margin_ratio: f64,
    pub max_active_grids: u32,
    pub new_entry_min_liquidation_distance: f64,
    pub reduce_only_min_liquidation_distance: f64,
    pub emergency_liquidation_distance: f64,
    pub daily_loss_stop_ratio: f64,
    pub margin_mode: String,
    pub hedge_mode: bool,
    pub leverage_cap: f64,
    pub max_automatic_restarts_per_day: u32,
    pub require_explicit_live_authorization: bool,
    pub allow_borrowing: bool,
    pub withdrawals_allowed: bool,
    pub ip_allowlist_required: bool,
}

impl Default for TliRiskPolicy {
    fn default() -> Self {
        Self {
            schema_version: TLI_POLICY_SCHEMA_VERSION.to_owned(),
            collateral_currency: "USDT".to_owned(),
            sizing_balance_cap: TLI_SIZING_BALANCE_CAP_USDT,
            max_initial_entry_sizing_balance_pct: TLI_MAX_INITIAL_ENTRY_SIZING_BALANCE_RATIO,
            max_total_exposure_ratio: TLI_MAX_AGGREGATE_EXPOSURE_RATIO,
            max_symbol_exposure_ratio: TLI_MAX_SYMBOL_EXPOSURE_RATIO,
            max_used_margin_ratio: TLI_MAX_USED_MARGIN_RATIO,
            max_active_grids: TLI_MAX_ACTIVE_GRIDS,
            new_entry_min_liquidation_distance: TLI_MIN_ENTRY_LIQUIDATION_DISTANCE,
            reduce_only_min_liquidation_distance: TLI_MIN_REDUCE_ONLY_LIQUIDATION_DISTANCE,
            emergency_liquidation_distance: TLI_MIN_EMERGENCY_LIQUIDATION_DISTANCE,
            daily_loss_stop_ratio: TLI_MAX_DAILY_LOSS_RATIO,
            margin_mode: "isolated".to_owned(),
            hedge_mode: true,
            leverage_cap: TLI_MAX_LEVERAGE_CAP,
            max_automatic_restarts_per_day: 1,
            require_explicit_live_authorization: true,
            allow_borrowing: false,
            withdrawals_allowed: false,
            ip_allowlist_required: true,
        }
    }
}

/// Stable reason codes for a rejected action.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TliRiskReasonCode {
    AggregateExposureLimit,
    SymbolExposureLimit,
    UsedMarginLimit,
    ActiveGridLimit,
    InitialEntryLimit,
    DailyLossLimit,
    LiquidationDistanceLimit,
    LeverageLimit,
}

/// Decision for one requested action. Protective actions remain visible here
/// and are not removed when an entry is rejected.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TliActionDecision {
    pub action: TliRiskAction,
    pub allowed: bool,
    pub reasons: Vec<TliRiskReasonCode>,
}

/// Result for the complete action batch.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TliRiskDecision {
    /// False only when at least one position-adding action was rejected.
    pub allow_new_entries: bool,
    pub actions: Vec<TliActionDecision>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum TliRiskError {
    InvalidField {
        field: String,
        message: String,
    },
    PolicyWouldRelaxLimit {
        field: String,
        configured: String,
        hard_limit: String,
    },
}

impl fmt::Display for TliRiskError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidField { field, message } => write!(f, "{field} {message}"),
            Self::PolicyWouldRelaxLimit {
                field,
                configured,
                hard_limit,
            } => write!(
                f,
                "{field} would relax the TLI v8 limit: configured={configured}, hard_limit={hard_limit}"
            ),
        }
    }
}

impl Error for TliRiskError {}

fn invalid(field: impl Into<String>, message: impl Into<String>) -> TliRiskError {
    TliRiskError::InvalidField {
        field: field.into(),
        message: message.into(),
    }
}

fn require_finite_nonnegative(field: impl Into<String>, value: f64) -> Result<(), TliRiskError> {
    let field = field.into();
    if !value.is_finite() {
        return Err(invalid(field, "must be finite"));
    }
    if value < 0.0 {
        return Err(invalid(field, "must be >= 0"));
    }
    Ok(())
}

fn require_fraction(field: impl Into<String>, value: f64) -> Result<(), TliRiskError> {
    let field = field.into();
    require_finite_nonnegative(field.clone(), value)?;
    if value > 1.0 {
        return Err(invalid(field, "must be <= 1.0"));
    }
    Ok(())
}

fn require_positive_finite(field: impl Into<String>, value: f64) -> Result<(), TliRiskError> {
    let field = field.into();
    if !value.is_finite() {
        return Err(invalid(field, "must be finite"));
    }
    if value <= 0.0 {
        return Err(invalid(field, "must be > 0"));
    }
    Ok(())
}

fn require_symbol(field: impl Into<String>, symbol: &str) -> Result<(), TliRiskError> {
    if symbol.trim().is_empty() {
        return Err(invalid(field, "must not be empty"));
    }
    Ok(())
}

fn bounded_max_policy(field: &str, configured: f64, hard_limit: f64) -> Result<(), TliRiskError> {
    require_positive_finite(field, configured)?;
    if configured > 1.0 {
        return Err(invalid(field, "must be <= 1.0"));
    }
    if configured > hard_limit {
        return Err(TliRiskError::PolicyWouldRelaxLimit {
            field: field.to_owned(),
            configured: configured.to_string(),
            hard_limit: hard_limit.to_string(),
        });
    }
    Ok(())
}

fn bounded_min_policy(field: &str, configured: f64, hard_floor: f64) -> Result<(), TliRiskError> {
    require_positive_finite(field, configured)?;
    if configured > 1.0 {
        return Err(invalid(field, "must be <= 1.0"));
    }
    if configured < hard_floor {
        return Err(TliRiskError::PolicyWouldRelaxLimit {
            field: field.to_owned(),
            configured: configured.to_string(),
            hard_limit: hard_floor.to_string(),
        });
    }
    Ok(())
}

fn validate_policy(policy: &TliRiskPolicy) -> Result<(), TliRiskError> {
    if policy.schema_version != TLI_POLICY_SCHEMA_VERSION {
        return Err(invalid(
            "schema_version",
            format!("must be {TLI_POLICY_SCHEMA_VERSION}"),
        ));
    }
    if policy.collateral_currency != "USDT" {
        return Err(invalid("collateral_currency", "must be USDT"));
    }
    require_positive_finite("sizing_balance_cap", policy.sizing_balance_cap)?;
    if policy.sizing_balance_cap > TLI_SIZING_BALANCE_CAP_USDT {
        return Err(TliRiskError::PolicyWouldRelaxLimit {
            field: "sizing_balance_cap".to_owned(),
            configured: policy.sizing_balance_cap.to_string(),
            hard_limit: TLI_SIZING_BALANCE_CAP_USDT.to_string(),
        });
    }
    bounded_max_policy(
        "max_initial_entry_sizing_balance_pct",
        policy.max_initial_entry_sizing_balance_pct,
        TLI_MAX_INITIAL_ENTRY_SIZING_BALANCE_RATIO,
    )?;
    bounded_max_policy(
        "max_total_exposure_ratio",
        policy.max_total_exposure_ratio,
        TLI_MAX_AGGREGATE_EXPOSURE_RATIO,
    )?;
    bounded_max_policy(
        "max_symbol_exposure_ratio",
        policy.max_symbol_exposure_ratio,
        TLI_MAX_SYMBOL_EXPOSURE_RATIO,
    )?;
    bounded_max_policy(
        "max_used_margin_ratio",
        policy.max_used_margin_ratio,
        TLI_MAX_USED_MARGIN_RATIO,
    )?;
    if policy.max_active_grids == 0 {
        return Err(invalid("max_active_grids", "must be in [1, 2]"));
    }
    if policy.max_active_grids > TLI_MAX_ACTIVE_GRIDS {
        return Err(TliRiskError::PolicyWouldRelaxLimit {
            field: "max_active_grids".to_owned(),
            configured: policy.max_active_grids.to_string(),
            hard_limit: TLI_MAX_ACTIVE_GRIDS.to_string(),
        });
    }
    bounded_max_policy(
        "daily_loss_stop_ratio",
        policy.daily_loss_stop_ratio,
        TLI_MAX_DAILY_LOSS_RATIO,
    )?;
    bounded_min_policy(
        "new_entry_min_liquidation_distance",
        policy.new_entry_min_liquidation_distance,
        TLI_MIN_ENTRY_LIQUIDATION_DISTANCE,
    )?;
    bounded_min_policy(
        "reduce_only_min_liquidation_distance",
        policy.reduce_only_min_liquidation_distance,
        TLI_MIN_REDUCE_ONLY_LIQUIDATION_DISTANCE,
    )?;
    bounded_min_policy(
        "emergency_liquidation_distance",
        policy.emergency_liquidation_distance,
        TLI_MIN_EMERGENCY_LIQUIDATION_DISTANCE,
    )?;
    if !(policy.emergency_liquidation_distance <= policy.reduce_only_min_liquidation_distance
        && policy.reduce_only_min_liquidation_distance <= policy.new_entry_min_liquidation_distance)
    {
        return Err(invalid(
            "liquidation distances",
            "must satisfy emergency <= reduce_only <= new_entry",
        ));
    }
    if policy.margin_mode != "isolated" {
        return Err(invalid("margin_mode", "must be isolated"));
    }
    if !policy.hedge_mode {
        return Err(invalid("hedge_mode", "must be true"));
    }
    require_positive_finite("leverage_cap", policy.leverage_cap)?;
    if policy.leverage_cap > TLI_MAX_LEVERAGE_CAP {
        return Err(TliRiskError::PolicyWouldRelaxLimit {
            field: "leverage_cap".to_owned(),
            configured: policy.leverage_cap.to_string(),
            hard_limit: TLI_MAX_LEVERAGE_CAP.to_string(),
        });
    }
    if policy.max_automatic_restarts_per_day > 1 {
        return Err(TliRiskError::PolicyWouldRelaxLimit {
            field: "max_automatic_restarts_per_day".to_owned(),
            configured: policy.max_automatic_restarts_per_day.to_string(),
            hard_limit: "1".to_owned(),
        });
    }
    if !policy.require_explicit_live_authorization {
        return Err(invalid(
            "require_explicit_live_authorization",
            "must be true",
        ));
    }
    if policy.allow_borrowing {
        return Err(invalid("allow_borrowing", "must be false"));
    }
    if policy.withdrawals_allowed {
        return Err(invalid("withdrawals_allowed", "must be false"));
    }
    if !policy.ip_allowlist_required {
        return Err(invalid("ip_allowlist_required", "must be true"));
    }
    Ok(())
}

fn validate_snapshot(snapshot: &TliPortfolioSnapshot) -> Result<(), TliRiskError> {
    require_finite_nonnegative("used_margin_usdt", snapshot.used_margin_usdt)?;
    require_finite_nonnegative("daily_loss_usdt", snapshot.daily_loss_usdt)?;

    for (index, position) in snapshot.positions.iter().enumerate() {
        require_symbol(format!("positions[{index}].symbol"), &position.symbol)?;
        require_finite_nonnegative(
            format!("positions[{index}].exposure_usdt"),
            position.exposure_usdt,
        )?;
    }

    for (index, action) in snapshot.actions.iter().enumerate() {
        let prefix = format!("actions[{index}]");
        require_symbol(format!("{prefix}.symbol"), &action.symbol)?;
        require_finite_nonnegative(format!("{prefix}.exposure_usdt"), action.exposure_usdt)?;
        require_finite_nonnegative(format!("{prefix}.margin_usdt"), action.margin_usdt)?;
        require_fraction(
            format!("{prefix}.liquidation_distance_pct"),
            action.liquidation_distance_pct,
        )?;
        require_positive_finite(format!("{prefix}.leverage"), action.leverage)?;
        if action.kind.is_entry() && action.exposure_usdt <= 0.0 {
            return Err(invalid(
                format!("{prefix}.exposure_usdt"),
                "must be > 0 for an entry",
            ));
        }
    }
    Ok(())
}

#[derive(Debug, Default)]
struct RiskState {
    aggregate_exposure_usdt: f64,
    symbol_exposure_usdt: BTreeMap<String, f64>,
    used_margin_usdt: f64,
    active_grids: u32,
}

fn add_finite(field: impl Into<String>, current: f64, addition: f64) -> Result<f64, TliRiskError> {
    let result = current + addition;
    if !result.is_finite() {
        return Err(invalid(field, "sum must be finite"));
    }
    Ok(result)
}

fn state_from_snapshot(snapshot: &TliPortfolioSnapshot) -> Result<RiskState, TliRiskError> {
    let mut state = RiskState {
        used_margin_usdt: snapshot.used_margin_usdt,
        active_grids: snapshot.active_grids,
        ..RiskState::default()
    };
    for (index, position) in snapshot.positions.iter().enumerate() {
        state.aggregate_exposure_usdt = add_finite(
            "positions.exposure_usdt",
            state.aggregate_exposure_usdt,
            position.exposure_usdt,
        )?;
        let symbol_exposure = state
            .symbol_exposure_usdt
            .get(&position.symbol)
            .copied()
            .unwrap_or(0.0);
        let symbol_exposure = add_finite(
            format!("positions[{index}].exposure_usdt"),
            symbol_exposure,
            position.exposure_usdt,
        )?;
        state
            .symbol_exposure_usdt
            .insert(position.symbol.clone(), symbol_exposure);
    }
    Ok(state)
}

fn add_reason(reasons: &mut Vec<TliRiskReasonCode>, reason: TliRiskReasonCode) {
    if !reasons.contains(&reason) {
        reasons.push(reason);
    }
}

fn required_liquidation_distance(kind: TliActionKind, policy: &TliRiskPolicy) -> f64 {
    match kind {
        TliActionKind::InitialEntry | TliActionKind::GridEntry => {
            policy.new_entry_min_liquidation_distance
        }
        TliActionKind::Close | TliActionKind::Reduce => policy.reduce_only_min_liquidation_distance,
        TliActionKind::Panic => policy.emergency_liquidation_distance,
    }
}

/// Evaluate a complete TLI portfolio snapshot without invoking exchange I/O,
/// the Python orchestrator, or the existing Rust risk engine.
pub fn evaluate_tli_portfolio_risk(
    snapshot: &TliPortfolioSnapshot,
    policy: &TliRiskPolicy,
) -> Result<TliRiskDecision, TliRiskError> {
    validate_policy(policy)?;
    validate_snapshot(snapshot)?;

    let mut state = state_from_snapshot(snapshot)?;
    let aggregate_limit_usdt = policy.sizing_balance_cap * policy.max_total_exposure_ratio;
    let symbol_limit_usdt = policy.sizing_balance_cap * policy.max_symbol_exposure_ratio;
    let used_margin_limit_usdt = policy.sizing_balance_cap * policy.max_used_margin_ratio;
    let initial_entry_limit_usdt =
        policy.sizing_balance_cap * policy.max_initial_entry_sizing_balance_pct;
    let daily_loss_limit_usdt = policy.sizing_balance_cap * policy.daily_loss_stop_ratio;
    for (field, value) in [
        ("aggregate exposure limit", aggregate_limit_usdt),
        ("symbol exposure limit", symbol_limit_usdt),
        ("used margin limit", used_margin_limit_usdt),
        ("initial entry limit", initial_entry_limit_usdt),
        ("daily loss limit", daily_loss_limit_usdt),
    ] {
        if !value.is_finite() {
            return Err(invalid(field, "must be finite"));
        }
    }
    let mut decisions = Vec::with_capacity(snapshot.actions.len());
    let mut rejected_entry = false;

    for action in &snapshot.actions {
        let mut reasons = Vec::new();
        let required_distance = required_liquidation_distance(action.kind, policy);
        if action.liquidation_distance_pct < required_distance {
            add_reason(&mut reasons, TliRiskReasonCode::LiquidationDistanceLimit);
        }

        if action.kind.is_entry() {
            let projected_aggregate = add_finite(
                "projected aggregate exposure",
                state.aggregate_exposure_usdt,
                action.exposure_usdt,
            )?;
            if projected_aggregate > aggregate_limit_usdt {
                add_reason(&mut reasons, TliRiskReasonCode::AggregateExposureLimit);
            }

            let current_symbol_exposure = state
                .symbol_exposure_usdt
                .get(&action.symbol)
                .copied()
                .unwrap_or(0.0);
            let projected_symbol = add_finite(
                "projected symbol exposure",
                current_symbol_exposure,
                action.exposure_usdt,
            )?;
            if projected_symbol > symbol_limit_usdt {
                add_reason(&mut reasons, TliRiskReasonCode::SymbolExposureLimit);
            }

            let projected_margin = add_finite(
                "projected used margin",
                state.used_margin_usdt,
                action.margin_usdt,
            )?;
            if projected_margin > used_margin_limit_usdt {
                add_reason(&mut reasons, TliRiskReasonCode::UsedMarginLimit);
            }

            let projected_grids = state
                .active_grids
                .checked_add(u32::from(action.opens_grid))
                .ok_or_else(|| invalid("projected active grids", "overflowed"))?;
            if projected_grids > policy.max_active_grids {
                add_reason(&mut reasons, TliRiskReasonCode::ActiveGridLimit);
            }

            if snapshot.daily_loss_usdt > daily_loss_limit_usdt {
                add_reason(&mut reasons, TliRiskReasonCode::DailyLossLimit);
            }

            if action.kind == TliActionKind::InitialEntry
                && action.exposure_usdt > initial_entry_limit_usdt
            {
                add_reason(&mut reasons, TliRiskReasonCode::InitialEntryLimit);
            }

            if action.leverage > policy.leverage_cap {
                add_reason(&mut reasons, TliRiskReasonCode::LeverageLimit);
            }

            if reasons.is_empty() {
                state.aggregate_exposure_usdt = projected_aggregate;
                state
                    .symbol_exposure_usdt
                    .insert(action.symbol.clone(), projected_symbol);
                state.used_margin_usdt = projected_margin;
                state.active_grids = projected_grids;
            } else {
                rejected_entry = true;
            }
        }

        decisions.push(TliActionDecision {
            action: action.clone(),
            allowed: reasons.is_empty(),
            reasons,
        });
    }

    Ok(TliRiskDecision {
        allow_new_entries: !rejected_entry,
        actions: decisions,
    })
}

/// PyO3-compatible JSON boundary for the typed Rust evaluator.
#[pyfunction(name = "evaluate_tli_portfolio_risk")]
pub fn evaluate_tli_portfolio_risk_py(snapshot_json: &str, policy_json: &str) -> PyResult<String> {
    let snapshot: TliPortfolioSnapshot = serde_json::from_str(snapshot_json).map_err(|error| {
        PyValueError::new_err(format!("invalid TLI portfolio snapshot: {error}"))
    })?;
    let policy: TliRiskPolicy = serde_json::from_str(policy_json)
        .map_err(|error| PyValueError::new_err(format!("invalid TLI risk policy: {error}")))?;
    let decision = evaluate_tli_portfolio_risk(&snapshot, &policy)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    serde_json::to_string(&decision)
        .map_err(|error| PyValueError::new_err(format!("serialize TLI risk decision: {error}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> TliRiskPolicy {
        TliRiskPolicy::default()
    }

    fn position(symbol: &str, side: TliPositionSide, exposure_usdt: f64) -> TliPositionSnapshot {
        TliPositionSnapshot {
            symbol: symbol.to_owned(),
            side,
            exposure_usdt,
        }
    }

    fn action(
        symbol: &str,
        kind: TliActionKind,
        exposure_usdt: f64,
        margin_usdt: f64,
        liquidation_distance_pct: f64,
        leverage: f64,
        opens_grid: bool,
    ) -> TliRiskAction {
        TliRiskAction {
            symbol: symbol.to_owned(),
            side: TliPositionSide::Long,
            kind,
            exposure_usdt,
            margin_usdt,
            liquidation_distance_pct,
            leverage,
            opens_grid,
        }
    }

    fn snapshot(
        positions: Vec<TliPositionSnapshot>,
        used_margin_usdt: f64,
        active_grids: u32,
        daily_loss_usdt: f64,
        actions: Vec<TliRiskAction>,
    ) -> TliPortfolioSnapshot {
        TliPortfolioSnapshot {
            positions,
            used_margin_usdt,
            active_grids,
            daily_loss_usdt,
            actions,
        }
    }

    fn evaluate(snapshot: TliPortfolioSnapshot) -> TliRiskDecision {
        evaluate_tli_portfolio_risk(&snapshot, &policy()).expect("valid TLI test input")
    }

    fn only_action(snapshot: TliPortfolioSnapshot) -> TliActionDecision {
        let decision = evaluate(snapshot);
        assert_eq!(decision.actions.len(), 1);
        decision.actions.into_iter().next().unwrap()
    }

    #[test]
    fn aggregate_exposure_includes_long_and_short_and_allows_boundary() {
        let accepted = evaluate(snapshot(
            vec![
                position("BTC", TliPositionSide::Long, 400.0),
                position("ETH", TliPositionSide::Short, 400.0),
            ],
            100.0,
            0,
            0.0,
            vec![],
        ));
        assert!(accepted.allow_new_entries);

        let rejected = only_action(snapshot(
            vec![
                position("BTC", TliPositionSide::Long, 400.0),
                position("ETH", TliPositionSide::Short, 400.0),
            ],
            100.0,
            0,
            0.0,
            vec![action(
                "SOL",
                TliActionKind::InitialEntry,
                10.0,
                10.0,
                0.30,
                3.0,
                true,
            )],
        ));
        assert!(!rejected.allowed);
        assert!(rejected
            .reasons
            .contains(&TliRiskReasonCode::AggregateExposureLimit));
    }

    #[test]
    fn symbol_exposure_limit_is_gross_across_sides() {
        let accepted = only_action(snapshot(
            vec![position("BTC", TliPositionSide::Long, 490.0)],
            100.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::GridEntry,
                10.0,
                10.0,
                0.30,
                3.0,
                false,
            )],
        ));
        assert!(accepted.allowed);

        let rejected = only_action(snapshot(
            vec![
                position("BTC", TliPositionSide::Long, 250.0),
                position("BTC", TliPositionSide::Short, 250.0),
            ],
            100.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::GridEntry,
                10.0,
                10.0,
                0.30,
                3.0,
                false,
            )],
        ));
        assert!(!rejected.allowed);
        assert!(rejected
            .reasons
            .contains(&TliRiskReasonCode::SymbolExposureLimit));
    }

    #[test]
    fn used_margin_limit_allows_boundary_and_rejects_overage() {
        let accepted = only_action(snapshot(
            vec![],
            350.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::GridEntry,
                10.0,
                0.0,
                0.30,
                3.0,
                false,
            )],
        ));
        assert!(accepted.allowed);

        let rejected = only_action(snapshot(
            vec![],
            350.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::GridEntry,
                10.0,
                1.0,
                0.30,
                3.0,
                false,
            )],
        ));
        assert!(!rejected.allowed);
        assert!(rejected
            .reasons
            .contains(&TliRiskReasonCode::UsedMarginLimit));
    }

    #[test]
    fn active_grid_limit_counts_only_new_grids() {
        let accepted = only_action(snapshot(
            vec![],
            100.0,
            2,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::GridEntry,
                10.0,
                10.0,
                0.30,
                3.0,
                false,
            )],
        ));
        assert!(accepted.allowed);

        let rejected = only_action(snapshot(
            vec![],
            100.0,
            2,
            0.0,
            vec![action(
                "ETH",
                TliActionKind::InitialEntry,
                10.0,
                10.0,
                0.30,
                3.0,
                true,
            )],
        ));
        assert!(!rejected.allowed);
        assert!(rejected
            .reasons
            .contains(&TliRiskReasonCode::ActiveGridLimit));
    }

    #[test]
    fn initial_entry_limit_is_separate_from_grid_entries() {
        let accepted = only_action(snapshot(
            vec![],
            100.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::InitialEntry,
                30.0,
                10.0,
                0.30,
                3.0,
                true,
            )],
        ));
        assert!(accepted.allowed);

        let rejected = only_action(snapshot(
            vec![],
            100.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::InitialEntry,
                30.1,
                10.0,
                0.30,
                3.0,
                true,
            )],
        ));
        assert!(!rejected.allowed);
        assert!(rejected
            .reasons
            .contains(&TliRiskReasonCode::InitialEntryLimit));

        let grid = only_action(snapshot(
            vec![],
            100.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::GridEntry,
                40.0,
                10.0,
                0.30,
                3.0,
                false,
            )],
        ));
        assert!(grid.allowed);
    }

    #[test]
    fn daily_loss_limit_blocks_new_entries() {
        let accepted = only_action(snapshot(
            vec![],
            100.0,
            0,
            20.0,
            vec![action(
                "BTC",
                TliActionKind::GridEntry,
                10.0,
                10.0,
                0.30,
                3.0,
                false,
            )],
        ));
        assert!(accepted.allowed);

        let rejected = only_action(snapshot(
            vec![],
            100.0,
            0,
            20.1,
            vec![action(
                "BTC",
                TliActionKind::GridEntry,
                10.0,
                10.0,
                0.30,
                3.0,
                false,
            )],
        ));
        assert!(!rejected.allowed);
        assert!(rejected
            .reasons
            .contains(&TliRiskReasonCode::DailyLossLimit));
    }

    #[test]
    fn liquidation_distance_uses_entry_reduce_and_panic_floors() {
        let entry_at_floor = only_action(snapshot(
            vec![],
            0.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::InitialEntry,
                10.0,
                10.0,
                0.30,
                3.0,
                true,
            )],
        ));
        assert!(entry_at_floor.allowed);

        let entry_below_floor = only_action(snapshot(
            vec![],
            0.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::InitialEntry,
                10.0,
                10.0,
                0.299,
                3.0,
                true,
            )],
        ));
        assert!(!entry_below_floor.allowed);

        let reduce_at_floor = only_action(snapshot(
            vec![],
            1.0,
            3,
            1.0,
            vec![action(
                "BTC",
                TliActionKind::Reduce,
                1000.0,
                1000.0,
                0.20,
                10.0,
                true,
            )],
        ));
        assert!(reduce_at_floor.allowed);

        let reduce_below_floor = only_action(snapshot(
            vec![],
            1.0,
            3,
            1.0,
            vec![action(
                "BTC",
                TliActionKind::Reduce,
                1000.0,
                1000.0,
                0.199,
                10.0,
                true,
            )],
        ));
        assert!(!reduce_below_floor.allowed);

        let panic_at_floor = only_action(snapshot(
            vec![],
            1.0,
            3,
            1.0,
            vec![action(
                "BTC",
                TliActionKind::Panic,
                1000.0,
                1000.0,
                0.15,
                10.0,
                true,
            )],
        ));
        assert!(panic_at_floor.allowed);

        let panic_below_floor = only_action(snapshot(
            vec![],
            1.0,
            3,
            1.0,
            vec![action(
                "BTC",
                TliActionKind::Panic,
                1000.0,
                1000.0,
                0.149,
                10.0,
                true,
            )],
        ));
        assert!(!panic_below_floor.allowed);
    }

    #[test]
    fn leverage_limit_is_three_x() {
        let accepted = only_action(snapshot(
            vec![],
            100.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::GridEntry,
                10.0,
                10.0,
                0.30,
                3.0,
                false,
            )],
        ));
        assert!(accepted.allowed);

        let rejected = only_action(snapshot(
            vec![],
            100.0,
            0,
            0.0,
            vec![action(
                "BTC",
                TliActionKind::GridEntry,
                10.0,
                10.0,
                0.30,
                3.01,
                false,
            )],
        ));
        assert!(!rejected.allowed);
        assert!(rejected.reasons.contains(&TliRiskReasonCode::LeverageLimit));
    }

    #[test]
    fn protective_actions_survive_entry_limit_breaches() {
        let decision = evaluate(snapshot(
            vec![
                position("BTC", TliPositionSide::Long, 700.0),
                position("ETH", TliPositionSide::Short, 700.0),
            ],
            800.0,
            4,
            100.0,
            vec![
                action(
                    "SOL",
                    TliActionKind::InitialEntry,
                    10.0,
                    10.0,
                    0.30,
                    3.0,
                    true,
                ),
                action(
                    "BTC",
                    TliActionKind::Close,
                    1000.0,
                    1000.0,
                    0.20,
                    20.0,
                    true,
                ),
                action(
                    "ETH",
                    TliActionKind::Reduce,
                    1000.0,
                    1000.0,
                    0.20,
                    20.0,
                    true,
                ),
                action(
                    "SOL",
                    TliActionKind::Panic,
                    1000.0,
                    1000.0,
                    0.15,
                    20.0,
                    true,
                ),
            ],
        ));

        assert!(!decision.allow_new_entries);
        assert_eq!(decision.actions.len(), 4);
        assert!(!decision.actions[0].allowed);
        assert!(decision.actions[1..].iter().all(|item| item.allowed));
        assert!(decision.actions[1..]
            .iter()
            .all(|item| item.reasons.is_empty()));
    }

    #[test]
    fn missing_or_non_numeric_required_json_fields_are_errors() {
        let missing_actions = r#"{
            "positions": [],
            "used_margin_usdt": 0.0,
            "active_grids": 0,
            "daily_loss_usdt": 0.0
        }"#;
        assert!(serde_json::from_str::<TliPortfolioSnapshot>(missing_actions).is_err());

        let non_numeric_margin = r#"{
            "positions": [],
            "used_margin_usdt": "not-a-number",
            "active_grids": 0,
            "daily_loss_usdt": 0.0,
            "actions": []
        }"#;
        assert!(serde_json::from_str::<TliPortfolioSnapshot>(non_numeric_margin).is_err());

        let mut invalid_snapshot = snapshot(vec![], 0.0, 0, 0.0, vec![]);
        invalid_snapshot.used_margin_usdt = f64::NAN;
        let error = evaluate_tli_portfolio_risk(&invalid_snapshot, &policy()).unwrap_err();
        assert!(error
            .to_string()
            .contains("used_margin_usdt must be finite"));
    }
}
