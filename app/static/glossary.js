// =========================================================
// glossary.js — MarketLab's financial dictionary, in ONE place.
//
// GLOSSARY[key] = {
//   term:  how it's written on screen ("P/E")
//   name:  the full name ("Price-to-earnings ratio")
//   short: one or two plain-English sentences (shown on hover)
//   why:   "Why this matters" (shown when you click the term)
// }
//
// PLAIN[key](value, ctx) returns a sentence describing what the number
// means FOR THIS COMPANY, e.g. "Has $1.00 of short-term assets for every
// $1 of bills due within a year." These are factual restatements of the
// number, never verdicts like "cheap", "risky" or "good".
//
// To explain a new term anywhere in the app: add it here, then use
// term("key") when building the HTML.
// =========================================================

const GLOSSARY = {
  revenue: {
    term: "Revenue", name: "Revenue (sales)",
    short: "All the money a company brings in from selling its products and services, before any costs are subtracted.",
    why: "Revenue is the top line: everything else (profit, cash flow) comes out of it. Steady revenue growth usually means more customers, higher prices, or new products are working.",
  },
  operating_income: {
    term: "Operating income", name: "Operating income (operating profit)",
    short: "Profit from the core business: revenue minus the costs of making products and running the company, before interest and taxes.",
    why: "It shows whether the business itself makes money, separate from how it's financed (debt) or taxed. A company can have rising revenue but falling operating income if costs grow faster.",
  },
  net_income: {
    term: "Net income", name: "Net income (the bottom line)",
    short: "What's left for shareholders after every cost, interest payment and tax. Negative net income is a loss.",
    why: "Net income is the profit figure most valuation ratios (like P/E) are built on. It can be affected by one-off items, so it helps to compare it with operating income and cash flow.",
  },
  fcf: {
    term: "Free cash flow", name: "Free cash flow (FCF)",
    short: "Cash the business generates from operations minus what it spends on equipment and facilities (capital expenditures).",
    why: "Free cash flow is the cash a company actually has available to repay debt, buy back shares, pay dividends, make acquisitions or reinvest. Profit can be shaped by accounting choices; cash is harder to disguise. Companies with negative FCF are funding themselves from savings, borrowing or issuing new shares.",
  },
  gross_margin: {
    term: "Gross margin", name: "Gross margin",
    short: "The share of each sales dollar left after the direct cost of producing what was sold.",
    why: "High gross margins (common in software) leave more room to fund research, marketing and profit. Margins that shrink over time can signal pricing pressure or rising costs. Typical levels vary a lot by industry.",
  },
  operating_margin: {
    term: "Operating margin", name: "Operating margin",
    short: "Operating income as a share of revenue: how many cents of each sales dollar become operating profit.",
    why: "It shows how efficiently the whole business runs. Expanding operating margins mean profit is growing faster than sales.",
  },
  eps: {
    term: "EPS", name: "Earnings per share",
    short: "The company's profit divided by the number of shares. \"Diluted\" EPS also counts shares that could be created from options and convertible securities.",
    why: "EPS turns total profit into a per-share number, which is what a shareholder actually owns. Rising EPS means more profit per share; it can also rise when a company buys back its own shares.",
  },
  ttm: {
    term: "TTM", name: "Trailing twelve months",
    short: "Data covering the company's most recent 12 months, rather than a calendar or fiscal year.",
    why: "TTM figures are the freshest full-year view, because they include the latest quarters instead of waiting for the next annual report.",
  },
  pe: {
    term: "P/E", name: "Price-to-earnings ratio",
    short: "The share price divided by earnings per share over the last 12 months. It shows how many dollars investors pay for each $1 of yearly profit.",
    why: "A higher P/E often means investors expect strong future growth; a lower one can mean lower expectations or more perceived risk. What counts as \"high\" varies greatly between industries. P/E doesn't exist when a company is losing money.",
  },
  forward_pe: {
    term: "Forward P/E", name: "Forward price-to-earnings ratio",
    short: "The share price divided by analysts' estimate of earnings per share for the coming year. It uses forecasts, not results.",
    why: "Comparing forward P/E with the trailing P/E shows whether analysts expect profit to rise (forward lower) or fall (forward higher). Forecasts can be wrong.",
  },
  peg: {
    term: "PEG", name: "Price/earnings-to-growth ratio",
    short: "P/E divided by the expected yearly growth rate of earnings (in %). It tries to adjust P/E for growth.",
    why: "Two companies with the same P/E can look very different if one is growing much faster. PEG depends on growth estimates, which can change quickly.",
  },
  ps: {
    term: "P/S", name: "Price-to-sales ratio",
    short: "The company's market value divided by its revenue over the last 12 months.",
    why: "Useful for companies that don't have profits yet, where P/E doesn't work. A high P/S means investors expect revenue to grow a lot or margins to rise.",
  },
  pb: {
    term: "P/B", name: "Price-to-book ratio",
    short: "Share price divided by book value per share. Book value is what the balance sheet says shareholders own (assets minus liabilities).",
    why: "Most meaningful for asset-heavy businesses like banks. For companies whose value is in brands, software or people, book value understates what they're worth.",
  },
  ebitda: {
    term: "EBITDA", name: "Earnings before interest, taxes, depreciation and amortization",
    short: "A rough measure of operating cash earnings: operating profit with the non-cash costs of wearing out equipment and intangible assets added back.",
    why: "EBITDA lets you compare businesses with different debt levels and tax situations. It ignores the real cost of replacing equipment, so it flatters capital-heavy companies.",
  },
  ev: {
    term: "Enterprise value", name: "Enterprise value (EV)",
    short: "Market value of the shares plus debt minus cash. Roughly what it would cost to buy the whole business, debts included.",
    why: "EV accounts for debt and cash, so it compares companies with different balance sheets more fairly than market cap alone.",
  },
  ev_ebitda: {
    term: "EV/EBITDA", name: "Enterprise value to EBITDA",
    short: "Enterprise value divided by a year of EBITDA. A valuation multiple that includes debt and ignores how a company is financed.",
    why: "Often used to compare companies in the same industry, especially when debt levels differ. Not meaningful when EBITDA is negative.",
  },
  fcf_yield: {
    term: "FCF yield", name: "Free cash flow yield",
    short: "Free cash flow divided by the company's market value: how much cash the business produced relative to what the market says it's worth.",
    why: "It's like an earnings yield based on cash. Comparing it with interest rates or other companies shows how much cash you get per dollar of stock.",
  },
  market_cap: {
    term: "Market cap", name: "Market capitalization",
    short: "Share price × number of shares. The stock market's current value of the whole company.",
    why: "It sets the company's size: large caps tend to be more established, small caps tend to move more. Valuation ratios compare it with revenue, profit or cash.",
  },
  beta: {
    term: "Beta", name: "Beta",
    short: "How strongly a stock has historically moved relative to the overall market (S&P 500). Around 1 means similar swings; above 1, larger; below 1, smaller.",
    why: "Beta describes past volatility relative to the market, not the risk of the business itself. It's measured here over 5 years of monthly returns and can change.",
  },
  dividend_yield: {
    term: "Dividend yield", name: "Dividend yield",
    short: "The yearly dividend per share divided by the share price: cash paid to shareholders as a percentage of the stock's price.",
    why: "Dividends are a direct cash return. A dividend is only sustainable if free cash flow covers it over time.",
  },
  avg_volume: {
    term: "Avg volume", name: "Average daily trading volume",
    short: "How many shares change hands on a typical day (3-month average).",
    why: "Higher volume generally means it's easier to buy or sell without moving the price much.",
  },
  week52: {
    term: "52-week range", name: "52-week range",
    short: "The lowest and highest prices the stock traded at over the past year.",
    why: "It shows where today's price sits within the past year's swings. A wide range relative to the price means a volatile year.",
  },
  yoy: {
    term: "YoY", name: "Year over year",
    short: "Change compared with the same period one year earlier.",
    why: "Comparing with the same period a year ago removes seasonal effects (like holiday sales), so it shows the underlying trend.",
  },
  cagr: {
    term: "CAGR", name: "Compound annual growth rate",
    short: "The steady yearly growth rate that would take a value from its starting point to its ending point over several years.",
    why: "It smooths out bumpy years, making multi-year growth easier to compare.",
  },
  consensus: {
    term: "Consensus estimate", name: "Analyst consensus estimate",
    short: "The average forecast from Wall Street analysts who cover the company. A prediction, not a result.",
    why: "Stocks often react to results versus these expectations, not just to whether results are good. Fewer analysts means a less reliable average.",
  },
  cash: {
    term: "Cash & ST investments", name: "Cash and short-term investments",
    short: "Cash plus investments that can quickly be turned into cash (like Treasury bills).",
    why: "Cash gives a company flexibility to survive losses, invest, or repay debt without raising money.",
  },
  total_debt: {
    term: "Total debt", name: "Total debt",
    short: "Money the company has borrowed (loans and bonds), including lease obligations, both short- and long-term.",
    why: "Debt has to be repaid or refinanced and usually carries interest. It isn't bad in itself, but it reduces flexibility when business slows.",
  },
  net_cash: {
    term: "Net cash", name: "Net cash (or net debt)",
    short: "Cash and short-term investments minus total debt. Positive means more cash than debt; negative (net debt) means the opposite.",
    why: "A quick way to see whether a company could pay off all its debt with cash on hand.",
  },
  current_ratio: {
    term: "Current ratio", name: "Current ratio",
    short: "Short-term assets (cash, receivables, inventory) divided by bills due within a year.",
    why: "It's a rough check of whether a company can cover its near-term obligations. Some very strong companies run below 1 on purpose, so context matters.",
  },
  debt_equity: {
    term: "Debt/equity", name: "Debt-to-equity ratio",
    short: "Total debt divided by shareholders' equity (book value). How much of the business is funded by borrowing versus owners' money.",
    why: "Higher means more reliance on borrowed money. Companies that buy back lots of stock can have small or negative equity, which makes this ratio look extreme.",
  },
  interest_coverage: {
    term: "Interest coverage", name: "Interest coverage ratio",
    short: "Operating profit (EBIT) divided by interest expense: how many times over profit covers interest payments.",
    why: "It shows how comfortably a company can service its debt. Low coverage leaves little room if profit falls.",
  },
  fiscal_year: {
    term: "Fiscal year", name: "Fiscal year (FY)",
    short: "The 12-month period a company uses for its accounts. It doesn't have to match the calendar year (Apple's ends in late September).",
    why: "When comparing companies, check their fiscal years end at similar times.",
  },
  form_10k: {
    term: "10-K", name: "Form 10-K (annual report)",
    short: "The detailed annual report US companies must file with the SEC: business description, risks, audited financial statements.",
    why: "It's the most complete official source on a company. The \"Risk Factors\" and \"Management's Discussion\" sections are especially useful.",
  },
  form_10q: {
    term: "10-Q", name: "Form 10-Q (quarterly report)",
    short: "The quarterly report filed with the SEC for each of the first three quarters. Financial statements are reviewed, not fully audited.",
    why: "The most recent official snapshot of results between annual reports.",
  },
  form_8k: {
    term: "8-K", name: "Form 8-K (material event)",
    short: "A report companies must file within days of important events: earnings releases, executive changes, acquisitions, major contracts.",
    why: "8-Ks are where significant news becomes official.",
  },
};

// ---------------------------------------------------------
// What a number means for THIS company (factual, never a verdict)
// ---------------------------------------------------------
const PLAIN = {
  pe: (v) => {
    if (!isNum(v)) return null;
    if (v < 0) return "Not meaningful: the company lost money over the last 12 months.";
    return `The share price is about ${v.toFixed(0)}× the last 12 months' earnings per share.`;
  },
  forward_pe: (v, c) => {
    if (!isNum(v)) return null;
    if (v < 0) return "Analysts expect a loss over the coming year, so a forward P/E isn't meaningful.";
    let text = `About ${v.toFixed(0)}× analysts' expected earnings per share.`;
    if (isNum(c.pe) && c.pe > 0 && Math.abs(v - c.pe) / c.pe > 0.03) {
      text += v < c.pe ? " Lower than the trailing P/E, so analysts expect profit per share to rise."
                       : " Higher than the trailing P/E, so analysts expect profit per share to fall.";
    }
    return text;
  },
  eps: (v, c) => {
    if (!isNum(v)) return null;
    return v < 0 ? `Lost ${fmtPerShareText(-v, c.currency)} per share over the last 12 months.`
                 : `Earned ${fmtPerShareText(v, c.currency)} of profit per share over the last 12 months.`;
  },
  beta: (v) => {
    if (!isNum(v)) return null;
    if (v > 1.15) return `Has historically swung about ${v.toFixed(1)}× as much as the overall market.`;
    if (v < 0.85 && v >= 0) return "Has historically moved less than the overall market.";
    if (v < 0) return "Has historically tended to move opposite to the market.";
    return "Has historically moved roughly in line with the overall market.";
  },
  dividend_yield: (v, c) => isNum(v) && isNum(c.rate)
    ? `Pays about ${fmtPerShareText(c.rate, c.currency)} per share per year, ${fmtPct(v, false, 2)} of the current price.` : null,
  avg_volume: (v) => isNum(v) ? `About ${fmtCompact(v)} shares change hands on a typical day.` : null,
  ps: (v) => isNum(v) ? `Valued at about ${v.toFixed(1)}× its revenue from the last 12 months.` : null,
  pb: (v) => isNum(v) ? `Valued at about ${v.toFixed(1)}× its accounting net worth (book value).` : null,
  ev_ebitda: (v) => {
    if (!isNum(v)) return null;
    if (v < 0) return "Not meaningful: EBITDA is negative (an operating loss before non-cash costs).";
    return `The whole business (debt included, cash excluded) is valued at about ${v.toFixed(0)}× a year of EBITDA.`;
  },
  peg: (v) => isNum(v) ? `Its P/E is ${v.toFixed(1)}× its expected yearly earnings growth rate.` : null,
  fcf_yield: (v) => {
    if (!isNum(v)) return null;
    return v >= 0 ? `Last year's free cash flow equals ${fmtPct(v, false, 1)} of the company's current market value.`
                  : "Free cash flow was negative: the business consumed cash last year.";
  },
  net_cash: (v, c) => {
    if (!isNum(v)) return null;
    return v >= 0 ? `Holds ${fmtMoneyText(v, c.currency)} more in cash and short-term investments than it owes in debt.`
                  : `Owes ${fmtMoneyText(-v, c.currency)} more in debt than it holds in cash and short-term investments.`;
  },
  current_ratio: (v) => isNum(v) ? `Has $${v.toFixed(2)} of short-term assets for every $1 of bills due within a year.` : null,
  debt_equity: (v) => isNum(v) ? `Carries $${v.toFixed(2)} of debt for every $1 of shareholders' equity.` : null,
  interest_coverage: (v) => isNum(v) ? `Operating profit covered interest costs about ${v.toFixed(0)}× over.` : null,
  fcf: (v) => {
    if (!isNum(v)) return null;
    return v >= 0 ? "Generated cash after paying for operations and investment." : "Spent more cash than it brought in (cash burn).";
  },
  week52: (price, c) => {
    if (![price, c.low, c.high].every(isNum)) return null;
    const below = (c.high - price) / c.high;
    return below < 0.005 ? "Trading at its 52-week high." : `Trading ${fmtPct(below, false, 1)} below its 52-week high.`;
  },
  gross_margin: (v) => isNum(v) ? `Keeps about ${Math.round(v * 100)}¢ of each $1 of sales after direct production costs.` : null,
  operating_margin: (v) => {
    if (!isNum(v)) return null;
    if (v <= -1) return `Operating costs exceed revenue: about $${Math.abs(v).toFixed(2)} of operating loss per $1 of sales.`;
    return v >= 0 ? `About ${Math.round(v * 100)}¢ of each $1 of sales is left as operating profit.`
                  : `Loses about ${Math.round(-v * 100)}¢ on operations for each $1 of sales.`;
  },
};

// Builds the HTML for a glossary term: label + small ⓘ. Hover shows the short
// definition; click opens the full explanation (with "✦ Explain" for this company).
// `plain` (optional) = the company-specific sentence, shown in the popover too.
function term(key, label, extra = {}) {
  const entry = GLOSSARY[key];
  const text = label || (entry ? entry.term : key);
  if (!entry) return escapeHtml(text);
  const data = [
    `data-term="${key}"`,
    extra.value ? `data-value="${escapeHtml(extra.value)}"` : "",
    extra.plain ? `data-plain="${escapeHtml(extra.plain)}"` : "",
    extra.section ? `data-section="${escapeHtml(extra.section)}"` : "",
  ].join(" ");
  return `<span class="term" tabindex="0" role="button" ${data}>${escapeHtml(text)}<span class="info" aria-hidden="true">i</span></span>`;
}

// ---------- Strategy Lab terms ----------
Object.assign(GLOSSARY, {
  lab_mean: {
    term: "Average forward return", name: "Average (mean) forward return",
    short: "Add up the return after every historical episode and divide by the number of episodes.",
    why: "The average is pulled around by a few huge outcomes. Compare it with the median: if they differ a lot, a handful of episodes are driving the result.",
  },
  lab_median: {
    term: "Median", name: "Median forward return",
    short: "The middle outcome: half of the episodes did better, half did worse.",
    why: "Unlike the average, the median ignores how extreme the outliers were, so it shows the 'typical' episode.",
  },
  lab_positive: {
    term: "Positive rate", name: "Share of positive outcomes",
    short: "The percentage of episodes where the stock was higher at the end of the forward period than on the trigger day.",
    why: "A high average with a low positive rate means a few big winners carried many small losers, which feels very different to live through.",
  },
  lab_std: {
    term: "Std deviation", name: "Standard deviation",
    short: "How spread out the outcomes were around the average. Bigger means individual episodes varied more.",
    why: "Two ideas with the same average can be completely different if one has outcomes scattered from −30% to +40%.",
  },
  lab_ci: {
    term: "95% confidence interval", name: "95% confidence interval for the average",
    short: "A range for the true long-run average, given how many episodes we have and how spread out they were. Fewer episodes means a wider range.",
    why: "It does NOT mean 95% of future outcomes land inside it. It describes uncertainty about the average, not about any single episode.",
  },
  lab_n: {
    term: "Sample size", name: "Sample size (n)",
    short: "How many separate historical episodes the statistics are based on. Overlapping days are counted once.",
    why: "With few episodes, results can swing a lot from luck alone. Under ~30, treat every number here as rough.",
  },
});

// ---------- Robust statistics, risk, chart and earnings terms ----------
Object.assign(GLOSSARY, {
  trimmed_mean: {
    term: "Trimmed mean", name: "Trimmed mean",
    short: "The average after setting aside the most extreme outcomes on BOTH sides (e.g. the top and bottom 5%).",
    why: "If the trimmed mean is far from the ordinary average, a few extreme episodes are doing most of the work.",
  },
  winsorized_mean: {
    term: "Winsorized mean", name: "Winsorized mean",
    short: "The average after pulling the most extreme outcomes in to the nearest 'normal' value instead of removing them.",
    why: "Like the trimmed mean it reduces the pull of outliers, but keeps the sample size the same.",
  },
  bootstrap: {
    term: "Bootstrap interval", name: "Bootstrap confidence interval",
    short: "Re-draw the episodes at random (with replacement) thousands of times, recompute the statistic each time, and keep the middle 95% of results.",
    why: "It doesn't assume outcomes follow a bell curve, so it's more honest when returns are lopsided or have fat tails.",
  },
  rolling_returns: {
    term: "Holding-period returns", name: "Rolling holding-period returns",
    short: "The return you'd have had buying on any past day and holding for the chosen period, using every possible start day.",
    why: "Shows the range of experiences an investor could have had, not just the start-to-end return of one lucky or unlucky date.",
  },
  drawdown: {
    term: "Drawdown", name: "Drawdown",
    short: "How far the price is below its previous highest close.",
    why: "A 50% drawdown needs a 100% gain to get back to even. Recovery time matters as much as depth.",
  },
  volatility: {
    term: "Volatility", name: "Annualised volatility",
    short: "The standard deviation of daily returns, scaled to a year (× √252). Higher means bigger day-to-day swings.",
    why: "Volatility describes typical swings, not crashes. A stock can be calm for years and still fall 50% in a month.",
  },
  correlation: {
    term: "Correlation", name: "Correlation",
    short: "How consistently two things moved together day to day, from −1 (opposite) to +1 (in lockstep). 0 = no consistent relationship.",
    why: "Correlation describes the past relationship, not cause. It can change quickly, especially in a market sell-off.",
  },
  var: {
    term: "VaR", name: "Value at Risk (historical)",
    short: "On the worst 5% of past days, the stock fell at least this much in a single day.",
    why: "It's a threshold, not a worst case: losses beyond it happen, and that's what Expected Shortfall measures.",
  },
  es: {
    term: "Expected shortfall", name: "Expected shortfall (CVaR)",
    short: "The average loss on the days that were worse than the VaR threshold.",
    why: "It looks inside the tail, so it reacts to how bad the bad days were, not just how often they happened.",
  },
  downside_dev: {
    term: "Downside deviation", name: "Downside deviation",
    short: "Like volatility, but only counts the down days. Up-swings don't add to it.",
    why: "Many investors care about losses, not about upside surprises; this separates the two.",
  },
  sortino: {
    term: "Sortino ratio", name: "Sortino ratio",
    short: "Average annual return divided by downside deviation, over the same period. Higher = more return per unit of downside.",
    why: "It's backward-looking and sensitive to the period chosen, especially after a big run.",
  },
  short_interest: {
    term: "Short interest", name: "Short interest",
    short: "Shares currently sold short (borrowed and sold, betting on a fall), as a share of the shares available to trade.",
    why: "High short interest means many traders expect a decline, and it can also cause sharp rises if they rush to buy back.",
  },
  days_to_cover: {
    term: "Days to cover", name: "Days to cover",
    short: "Shares sold short divided by average daily volume: roughly how many days of normal trading it would take to buy them all back.",
    why: "A larger number means short sellers would find it harder to exit quickly.",
  },
  gap: {
    term: "Gap", name: "Overnight gap",
    short: "The difference between one day's close and the next day's opening price.",
    why: "Gaps happen when news arrives while the market is closed. Stop orders can't protect against them.",
  },
  dilution: {
    term: "Dilution", name: "Share dilution",
    short: "Growth in the number of shares outstanding. Each existing share then owns a smaller slice of the company.",
    why: "Companies that fund themselves by issuing stock can grow the business while per-share value grows more slowly.",
  },
  runway: {
    term: "Cash runway", name: "Cash runway",
    short: "Cash and short-term investments divided by the yearly cash burn (negative free cash flow).",
    why: "A rough measure only: burn rates change, and companies can raise money or cut costs.",
  },
  eps_surprise: {
    term: "Surprise", name: "Earnings surprise",
    short: "How much reported EPS differed from the analyst consensus estimate, as a percentage of the estimate.",
    why: "Markets often react to results relative to expectations, not to the result alone.",
  },
  sma: {
    term: "SMA", name: "Simple moving average",
    short: "The average closing price over the last N bars (e.g. 50 days), redrawn every bar.",
    why: "It smooths out daily noise; prices crossing it are a common (and not reliably predictive) signal.",
  },
  vwap: {
    term: "VWAP", name: "Volume-weighted average price",
    short: "The average price paid during the session, weighted by how many shares traded at each price. Resets each day.",
    why: "Traders use it as a reference for whether a price is high or low relative to the day's trading.",
  },
});
