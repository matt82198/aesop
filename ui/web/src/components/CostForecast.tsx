/**
 * Cost Trend Forecast — linear regression over daily spend with confidence band.
 *
 * Features:
 *  (a) Linear regression trend line over recent daily totals
 *  (b) End-of-wave (28d) projection with projected tokens
 *  (c) Ceiling line (if configured) and projections vs ceiling
 *  (d) 70%/90% confidence ribbon (residual-based band, labeled honestly)
 *  (e) Three-state contract: populated (with/without ceiling), empty, error
 *  (f) Accessible: role="img" + aria-label for the chart
 *
 * Data source: cost.daily_totals (YYYY-MM-DD keyed).
 */

import type { CostSummary } from '../lib/types';
import './CostForecast.css';

interface CostForecastProps {
  cost: CostSummary;
  ceilingTokens?: number; // optional token ceiling
}

interface TrendData {
  dailyBurn: number; // tokens per day
  slope: number; // linear regression slope
  intercept: number; // linear regression intercept
  residualStd: number; // standard deviation of residuals
  projectedEnd: number; // projected tokens at day 28
  daysWithData: number;
  confidence70: number; // upper band at 70% CI
  confidence90: number; // upper band at 90% CI
}

/**
 * Linear regression: y = mx + b
 * Returns slope, intercept, and residual std dev.
 */
function linearRegression(
  points: { x: number; y: number }[]
): { slope: number; intercept: number; residualStd: number } {
  if (points.length < 2) {
    return { slope: 0, intercept: 0, residualStd: 0 };
  }

  const n = points.length;
  let sumX = 0,
    sumY = 0,
    sumXY = 0,
    sumX2 = 0;

  for (const p of points) {
    sumX += p.x;
    sumY += p.y;
    sumXY += p.x * p.y;
    sumX2 += p.x * p.x;
  }

  const slope = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX);
  const intercept = (sumY - slope * sumX) / n;

  // Calculate residual standard deviation
  let sumResidualSq = 0;
  for (const p of points) {
    const predicted = slope * p.x + intercept;
    const residual = p.y - predicted;
    sumResidualSq += residual * residual;
  }
  const residualStd = Math.sqrt(sumResidualSq / (n - 1));

  return { slope, intercept, residualStd };
}

/**
 * Extract daily totals as time series and compute trend.
 */
function calculateTrend(cost: CostSummary): TrendData | null {
  const dailyTotals = cost.daily_totals || {};
  const dates = Object.keys(dailyTotals).sort();

  if (dates.length < 2) {
    return null; // Need at least 2 points for trend
  }

  // Convert to (x, y) points: x = day index, y = tokens per day
  const points = dates.map((date, index) => {
    const daily = dailyTotals[date];
    const tokensPerDay = (daily.tokens_in || 0) + (daily.tokens_out || 0);
    return { x: index, y: tokensPerDay };
  });

  const { slope, intercept, residualStd } = linearRegression(points);

  // Daily burn = average tokens per day
  const dailyBurn = points.reduce((sum, p) => sum + p.y, 0) / points.length;

  // Project to day 28 (end of wave)
  const projectedEnd = slope * (28 - 1) + intercept; // Extrapolate to day 28

  // Confidence bands: add multiples of residual std dev
  // ~68% for 1-sigma, ~95% for 2-sigma; we use ~1.04 for 70% and ~1.64 for 90%
  const confidence70 = projectedEnd + residualStd * 1.04;
  const confidence90 = projectedEnd + residualStd * 1.64;

  return {
    dailyBurn: Math.round(dailyBurn),
    slope,
    intercept,
    residualStd,
    projectedEnd: Math.round(Math.max(0, projectedEnd)),
    daysWithData: dates.length,
    confidence70: Math.round(Math.max(0, confidence70)),
    confidence90: Math.round(Math.max(0, confidence90)),
  };
}

/**
 * Render the forecast chart as pure SVG.
 */
function ForecastChart({
  trend,
  ceilingTokens,
}: {
  trend: TrendData;
  ceilingTokens?: number;
}): React.ReactNode {
  const SVG_WIDTH = 400;
  const SVG_HEIGHT = 200;
  const MARGIN = { top: 20, right: 20, bottom: 30, left: 60 };
  const CHART_WIDTH = SVG_WIDTH - MARGIN.left - MARGIN.right;
  const CHART_HEIGHT = SVG_HEIGHT - MARGIN.top - MARGIN.bottom;

  // Scale: x = 0..28 days, y = 0..max(ceiling or projected)
  const maxY = Math.max(trend.confidence90, ceilingTokens || trend.confidence90, 1);
  const scaleX = CHART_WIDTH / 28;
  const scaleY = CHART_HEIGHT / maxY;

  // Trend line: from day 0 to day 28
  const x0 = MARGIN.left + 0 * scaleX;
  const y0 = SVG_HEIGHT - MARGIN.bottom - trend.intercept * scaleY;
  const x28 = MARGIN.left + 28 * scaleX;
  const y28 = SVG_HEIGHT - MARGIN.bottom - (trend.slope * 27 + trend.intercept) * scaleY;

  // Confidence band: upper envelope (70% and 90%)
  const confidencePath70 = `M ${x0} ${SVG_HEIGHT - MARGIN.bottom - trend.confidence70 * scaleY} L ${x28} ${SVG_HEIGHT - MARGIN.bottom - trend.confidence70 * scaleY}`;
  const confidencePath90 = `M ${x0} ${SVG_HEIGHT - MARGIN.bottom - trend.confidence90 * scaleY} L ${x28} ${SVG_HEIGHT - MARGIN.bottom - trend.confidence90 * scaleY}`;

  // Ceiling line
  const ceilingY = SVG_HEIGHT - MARGIN.bottom - (ceilingTokens || 0) * scaleY;
  const ceilingPath =
    ceilingTokens !== undefined
      ? `M ${MARGIN.left} ${ceilingY} L ${x28} ${ceilingY}`
      : null;

  // Projected point at day 28
  const projX = x28;
  const projY = SVG_HEIGHT - MARGIN.bottom - trend.projectedEnd * scaleY;

  // Y-axis labels
  const yMax = Math.ceil(maxY / 1_000_000) * 1_000_000;
  const yStep = yMax / 4;
  const yLabels = [0, yStep, yStep * 2, yStep * 3, yStep * 4];

  return (
    <svg
      viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
      className="forecast-chart"
      role="img"
      aria-label={`Cost forecast: projected ${trend.projectedEnd.toLocaleString()} tokens at end of wave`}
    >
      {/* Grid lines */}
      {yLabels.map((y) => (
        <line
          key={`grid-${y}`}
          x1={MARGIN.left}
          y1={SVG_HEIGHT - MARGIN.bottom - y * scaleY}
          x2={SVG_WIDTH - MARGIN.right}
          y2={SVG_HEIGHT - MARGIN.bottom - y * scaleY}
          className="grid-line"
        />
      ))}

      {/* Confidence bands */}
      <path d={confidencePath90} className="confidence-band confidence-band-90" />
      <path d={confidencePath70} className="confidence-band confidence-band-70" />

      {/* Ceiling line (if configured) */}
      {ceilingPath && <path d={ceilingPath} className="ceiling-line" />}

      {/* Trend line */}
      <path d={`M ${x0} ${y0} L ${x28} ${y28}`} className="trend-line" />

      {/* Projected point */}
      <circle cx={projX} cy={projY} r="4" className="projected-point" />

      {/* Axes */}
      <line x1={MARGIN.left} y1={MARGIN.top} x2={MARGIN.left} y2={SVG_HEIGHT - MARGIN.bottom} className="axis" />
      <line x1={MARGIN.left} y1={SVG_HEIGHT - MARGIN.bottom} x2={SVG_WIDTH - MARGIN.right} y2={SVG_HEIGHT - MARGIN.bottom} className="axis" />

      {/* Y-axis labels */}
      {yLabels.map((y) => (
        <text
          key={`y-label-${y}`}
          x={MARGIN.left - 5}
          y={SVG_HEIGHT - MARGIN.bottom - y * scaleY + 4}
          className="axis-label"
          textAnchor="end"
        >
          {(y / 1_000_000).toFixed(0)}M
        </text>
      ))}

      {/* X-axis labels */}
      {[0, 7, 14, 21, 28].map((day) => (
        <text
          key={`x-label-${day}`}
          x={MARGIN.left + day * scaleX}
          y={SVG_HEIGHT - MARGIN.bottom + 20}
          className="axis-label"
          textAnchor="middle"
        >
          {day}d
        </text>
      ))}
    </svg>
  );
}

/**
 * Render forecast metrics and interpretation.
 */
function ForecastMetrics({
  trend,
  ceilingTokens,
}: {
  trend: TrendData;
  ceilingTokens?: number;
}): React.ReactNode {
  const ceilingMet = ceilingTokens && trend.projectedEnd > ceilingTokens;
  const confidence90Met = ceilingTokens && trend.confidence90 > ceilingTokens;

  return (
    <div className="forecast-metrics" data-testid="cost-forecast-metrics">
      <div className="metric-row">
        <span className="metric-label">Trend (daily burn):</span>
        <span className="metric-value">{trend.dailyBurn.toLocaleString()} tokens/day</span>
      </div>

      <div className="metric-row">
        <span className="metric-label">Projected end-of-wave (28d):</span>
        <span className={`metric-value ${ceilingMet ? 'alert' : ''}`}>
          {trend.projectedEnd.toLocaleString()} tokens
        </span>
      </div>

      <div className="metric-row">
        <span className="metric-label">90% confidence (upper bound):</span>
        <span className={`metric-value ${confidence90Met ? 'alert' : ''}`}>
          {trend.confidence90.toLocaleString()} tokens
        </span>
      </div>

      {ceilingTokens && (
        <>
          <div className="metric-row">
            <span className="metric-label">Ceiling:</span>
            <span className="metric-value">{ceilingTokens.toLocaleString()} tokens</span>
          </div>

          <div className={`metric-row ${ceilingMet ? 'alert' : 'safe'}`}>
            <span className="metric-label">Projected vs ceiling:</span>
            <span className="metric-value">
              {ceilingMet ? '⚠ Exceeds' : '✓ Within'}{' '}
              {Math.abs(trend.projectedEnd - ceilingTokens).toLocaleString()} tokens
            </span>
          </div>
        </>
      )}

      <p className="forecast-note">
        Trend: linear regression over {trend.daysWithData} day{trend.daysWithData === 1 ? '' : 's'}. Confidence band
        assumes residual variance; labeled honestly as statistical estimate, not guarantee.
      </p>
    </div>
  );
}

export function CostForecast({ cost, ceilingTokens }: CostForecastProps) {
  const trend = calculateTrend(cost);

  // Three states:
  // 1. Empty: no daily totals
  // 2. Populated: trend available
  // 3. No ceiling configured (but still shows projection)

  if (!trend) {
    return (
      <div className="cost-forecast" data-testid="cost-forecast-empty">
        <h4>Cost Trend Forecast</h4>
        <div className="forecast-unavailable">
          <p>Need at least 2 days of ledger data to calculate trend.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="cost-forecast" data-testid="cost-forecast-populated">
      <h4>Cost Trend Forecast</h4>

      <ForecastChart trend={trend} ceilingTokens={ceilingTokens} />

      <ForecastMetrics trend={trend} ceilingTokens={ceilingTokens} />

      {!ceilingTokens && (
        <div className="forecast-callout">
          <p>No ceiling configured. Configure a ceiling in Cost Analytics Panel props to see alerts.</p>
        </div>
      )}
    </div>
  );
}
