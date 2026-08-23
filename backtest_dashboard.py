"""
Backtest Dashboard Generator — Interactive HTML Report
======================================================
Reads the enriched CSV from delta_arb_backtest.py and generates a
self-contained, interactive HTML dashboard using Plotly.

Usage:
    python backtest_dashboard.py                        # uses default CSV path
    python backtest_dashboard.py path/to/results.csv    # custom CSV path
"""

import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly.express as px
except ImportError:
    print("ERROR: plotly is required. Install with: pip install plotly")
    sys.exit(1)


def load_data(csv_path):
    """Load and validate the backtest CSV."""
    df = pd.read_csv(csv_path)
    df['Entry Time'] = pd.to_datetime(df['Entry Time'])
    df['Exit Time'] = pd.to_datetime(df['Exit Time'])
    
    # Ensure required columns exist (backwards-compatible with old CSV format)
    if 'Call Entry Premium' not in df.columns:
        # Old CSV format — derive what we can
        df['Call Entry Premium'] = df['Total Premium Collected'] / 2
        df['Put Entry Premium'] = df['Total Premium Collected'] / 2
        df['Call Exit Premium'] = 0
        df['Put Exit Premium'] = 0
        df['Entry Slippage'] = df['Total Slippage Paid']
        df['Exit Slippage'] = 0
        df['Fees Paid'] = 0
        df['Underlying Exit Price'] = df['Underlying Entry Price']
        df['Call SL Hit'] = df['Exit Reason'].str.contains('Call SL|Both SL', case=False, na=False)
        df['Put SL Hit'] = df['Exit Reason'].str.contains('Put SL|Both SL', case=False, na=False)
        df['Trade Duration (hours)'] = (df['Exit Time'] - df['Entry Time']).dt.total_seconds() / 3600
    
    df['Month'] = df['Entry Time'].dt.strftime('%Y-%m')
    df['Month Name'] = df['Entry Time'].dt.strftime('%b %Y')
    df['Cumulative PnL'] = df['Net PnL'].cumsum()
    df['Trade #'] = range(1, len(df) + 1)
    df['Win'] = df['Net PnL'] > 0
    
    return df


def compute_metrics(df):
    """Compute all performance metrics."""
    total_trades = len(df)
    wins = df['Win'].sum()
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    cum_pnl = df['Net PnL'].sum()
    avg_pnl = df['Net PnL'].mean()
    median_pnl = df['Net PnL'].median()
    best_trade = df['Net PnL'].max()
    worst_trade = df['Net PnL'].min()
    
    avg_win = df.loc[df['Win'], 'Net PnL'].mean() if wins > 0 else 0
    avg_loss = df.loc[~df['Win'], 'Net PnL'].mean() if losses > 0 else 0
    
    # Profit Factor
    gross_profit = df.loc[df['Win'], 'Net PnL'].sum() if wins > 0 else 0
    gross_loss = abs(df.loc[~df['Win'], 'Net PnL'].sum()) if losses > 0 else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    # Max Drawdown
    cumulative = df['Cumulative PnL']
    running_max = cumulative.cummax()
    drawdown = cumulative - running_max
    max_drawdown = drawdown.min()
    max_dd_pct = (max_drawdown / running_max[drawdown.idxmin()]) * 100 if running_max[drawdown.idxmin()] != 0 else 0
    
    # Sharpe Ratio (annualized, assuming weekly trades)
    returns = df['Net PnL']
    sharpe = (returns.mean() / returns.std()) * np.sqrt(52) if returns.std() > 0 else 0
    
    # Win/Loss Streaks
    streaks = []
    current_streak = 0
    current_type = None
    for w in df['Win']:
        if w == current_type:
            current_streak += 1
        else:
            if current_type is not None:
                streaks.append((current_type, current_streak))
            current_type = w
            current_streak = 1
    if current_type is not None:
        streaks.append((current_type, current_streak))
    
    max_win_streak = max((s for t, s in streaks if t), default=0)
    max_loss_streak = max((s for t, s in streaks if not t), default=0)
    
    # Total costs
    total_slippage = df['Total Slippage Paid'].sum()
    total_fees = df['Fees Paid'].sum() if 'Fees Paid' in df.columns else 0
    total_premium = df['Total Premium Collected'].sum()
    
    # Expectancy
    expectancy = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)
    
    return {
        'total_trades': total_trades,
        'wins': wins,
        'losses': losses,
        'win_rate': win_rate,
        'cum_pnl': cum_pnl,
        'avg_pnl': avg_pnl,
        'median_pnl': median_pnl,
        'best_trade': best_trade,
        'worst_trade': worst_trade,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'max_drawdown': max_drawdown,
        'max_dd_pct': max_dd_pct,
        'sharpe': sharpe,
        'max_win_streak': max_win_streak,
        'max_loss_streak': max_loss_streak,
        'total_slippage': total_slippage,
        'total_fees': total_fees,
        'total_premium': total_premium,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'expectancy': expectancy,
    }


def compute_monthly(df):
    """Compute month-on-month breakdown."""
    monthly = df.groupby('Month').agg(
        trades=('Net PnL', 'count'),
        net_pnl=('Net PnL', 'sum'),
        avg_pnl=('Net PnL', 'mean'),
        win_rate=('Win', 'mean'),
        best_trade=('Net PnL', 'max'),
        worst_trade=('Net PnL', 'min'),
        premium_collected=('Total Premium Collected', 'sum'),
        total_slippage=('Total Slippage Paid', 'sum'),
    ).reset_index()
    monthly['win_rate'] = monthly['win_rate'] * 100
    monthly['cumulative_pnl'] = monthly['net_pnl'].cumsum()
    return monthly


def generate_dashboard(csv_path=None):
    """Generate the full interactive HTML dashboard."""
    
    if csv_path is None:
        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
                                "strangle_backtest_detailed_results.csv")
    
    if not os.path.exists(csv_path):
        print(f"ERROR: CSV file not found: {csv_path}")
        print("Run delta_arb_backtest.py first to generate the data.")
        return None
    
    df = load_data(csv_path)
    metrics = compute_metrics(df)
    monthly = compute_monthly(df)
    
    # ==========================================
    # Build all Plotly figures
    # ==========================================
    
    # Color palette
    GREEN = '#00E676'
    RED = '#FF5252'
    CYAN = '#00BCD4'
    AMBER = '#FFD740'
    PURPLE = '#B388FF'
    BG_DARK = '#0d1117'
    BG_CARD = '#161b22'
    BG_CHART = '#0d1117'
    GRID_COLOR = '#21262d'
    TEXT_COLOR = '#c9d1d9'
    TEXT_BRIGHT = '#f0f6fc'
    
    common_layout = dict(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor=BG_CHART,
        font=dict(family='Inter, system-ui, sans-serif', color=TEXT_COLOR, size=12),
        margin=dict(l=50, r=30, t=50, b=40),
        xaxis=dict(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR),
        yaxis=dict(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR),
    )
    
    # 1. EQUITY CURVE
    fig_equity = go.Figure()
    fig_equity.add_trace(go.Scatter(
        x=df['Exit Time'],
        y=df['Cumulative PnL'],
        mode='lines',
        line=dict(color=CYAN, width=2.5),
        fill='tozeroy',
        fillcolor='rgba(0,188,212,0.1)',
        name='Cumulative P&L',
        hovertemplate='<b>Trade #%{customdata[0]}</b><br>Date: %{x}<br>Cumulative P&L: $%{y:,.2f}<br>Trade P&L: $%{customdata[1]:,.2f}<extra></extra>',
        customdata=list(zip(df['Trade #'], df['Net PnL']))
    ))
    fig_equity.update_layout(
        title=dict(text='Equity Curve', font=dict(size=18, color=TEXT_BRIGHT)),
        xaxis_title='Date',
        yaxis_title='Cumulative P&L ($)',
        height=400,
        **common_layout
    )
    
    # 2. DRAWDOWN CHART
    running_max = df['Cumulative PnL'].cummax()
    drawdown = df['Cumulative PnL'] - running_max
    
    fig_drawdown = go.Figure()
    fig_drawdown.add_trace(go.Scatter(
        x=df['Exit Time'],
        y=drawdown,
        mode='lines',
        line=dict(color=RED, width=2),
        fill='tozeroy',
        fillcolor='rgba(255,82,82,0.15)',
        name='Drawdown',
        hovertemplate='Date: %{x}<br>Drawdown: $%{y:,.2f}<extra></extra>',
    ))
    fig_drawdown.update_layout(
        title=dict(text='Underwater Drawdown', font=dict(size=18, color=TEXT_BRIGHT)),
        xaxis_title='Date',
        yaxis_title='Drawdown ($)',
        height=300,
        **common_layout
    )
    
    # 3. MONTHLY P&L BAR CHART
    bar_colors = [GREEN if v >= 0 else RED for v in monthly['net_pnl']]
    
    fig_monthly = go.Figure()
    fig_monthly.add_trace(go.Bar(
        x=monthly['Month'],
        y=monthly['net_pnl'],
        marker_color=bar_colors,
        text=[f"${v:,.0f}" for v in monthly['net_pnl']],
        textposition='outside',
        textfont=dict(size=11),
        hovertemplate='<b>%{x}</b><br>Net P&L: $%{y:,.2f}<br>Trades: %{customdata[0]}<br>Win Rate: %{customdata[1]:.1f}%<extra></extra>',
        customdata=list(zip(monthly['trades'], monthly['win_rate'])),
        name='Monthly P&L'
    ))
    fig_monthly.update_layout(
        title=dict(text='Month-on-Month P&L', font=dict(size=18, color=TEXT_BRIGHT)),
        xaxis_title='Month',
        yaxis_title='Net P&L ($)',
        height=400,
        **common_layout
    )
    
    # 4. TRADE P&L DISTRIBUTION
    fig_dist = go.Figure()
    fig_dist.add_trace(go.Histogram(
        x=df['Net PnL'],
        nbinsx=30,
        marker_color=CYAN,
        marker_line=dict(color=BG_DARK, width=1),
        opacity=0.85,
        hovertemplate='P&L Range: $%{x:,.0f}<br>Count: %{y}<extra></extra>',
        name='P&L Distribution'
    ))
    fig_dist.add_vline(x=0, line_dash='dash', line_color=AMBER, line_width=1.5)
    fig_dist.add_vline(x=df['Net PnL'].mean(), line_dash='dot', line_color=GREEN, line_width=1.5,
                       annotation_text=f"Mean: ${df['Net PnL'].mean():,.0f}", annotation_position="top right")
    fig_dist.update_layout(
        title=dict(text='P&L Distribution', font=dict(size=18, color=TEXT_BRIGHT)),
        xaxis_title='Net P&L ($)',
        yaxis_title='Frequency',
        height=350,
        **common_layout
    )
    
    # 5. EXIT REASON PIE CHART
    exit_counts = df['Exit Reason'].value_counts()
    pie_colors = {
        'Expiry': '#00BCD4',
        'Call SL Hit': '#FF5252',
        'Put SL Hit': '#FFD740',
        'Both SL Hit': '#B388FF',
    }
    colors = [pie_colors.get(r, '#888') for r in exit_counts.index]
    
    fig_pie = go.Figure()
    fig_pie.add_trace(go.Pie(
        labels=exit_counts.index,
        values=exit_counts.values,
        marker=dict(colors=colors, line=dict(color=BG_DARK, width=2)),
        textinfo='label+percent',
        textfont=dict(size=13),
        hole=0.45,
        hovertemplate='<b>%{label}</b><br>Count: %{value}<br>Percentage: %{percent}<extra></extra>',
    ))
    fig_pie.update_layout(
        title=dict(text='Exit Reason Breakdown', font=dict(size=18, color=TEXT_BRIGHT)),
        height=350,
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(family='Inter, system-ui, sans-serif', color=TEXT_COLOR, size=12),
        margin=dict(l=30, r=30, t=50, b=30),
        showlegend=True,
        legend=dict(font=dict(size=12)),
    )
    
    # 6. PER-TRADE WATERFALL
    fig_waterfall = go.Figure()
    colors_wf = [GREEN if v >= 0 else RED for v in df['Net PnL']]
    fig_waterfall.add_trace(go.Bar(
        x=df['Trade #'],
        y=df['Net PnL'],
        marker_color=colors_wf,
        hovertemplate='<b>Trade #%{x}</b><br>P&L: $%{y:,.2f}<br>Entry: %{customdata[0]}<br>Exit: %{customdata[1]}<br>Reason: %{customdata[2]}<extra></extra>',
        customdata=list(zip(
            df['Entry Time'].dt.strftime('%Y-%m-%d %H:%M'),
            df['Exit Time'].dt.strftime('%Y-%m-%d %H:%M'),
            df['Exit Reason']
        )),
        name='Trade P&L'
    ))
    fig_waterfall.update_layout(
        title=dict(text='Per-Trade P&L', font=dict(size=18, color=TEXT_BRIGHT)),
        xaxis_title='Trade #',
        yaxis_title='Net P&L ($)',
        height=350,
        **common_layout
    )
    
    # 7. MONTHLY CUMULATIVE P&L LINE
    fig_monthly_cum = go.Figure()
    fig_monthly_cum.add_trace(go.Scatter(
        x=monthly['Month'],
        y=monthly['cumulative_pnl'],
        mode='lines+markers',
        line=dict(color=PURPLE, width=2.5),
        marker=dict(size=8, color=PURPLE, line=dict(color=BG_DARK, width=1.5)),
        hovertemplate='<b>%{x}</b><br>Cumulative P&L: $%{y:,.2f}<extra></extra>',
        name='Cumulative Monthly P&L'
    ))
    fig_monthly_cum.update_layout(
        title=dict(text='Monthly Cumulative P&L', font=dict(size=18, color=TEXT_BRIGHT)),
        xaxis_title='Month',
        yaxis_title='Cumulative P&L ($)',
        height=350,
        **common_layout
    )
    
    # ==========================================
    # Convert figures to HTML divs
    # ==========================================
    
    equity_html = fig_equity.to_html(full_html=False, include_plotlyjs=False, div_id='equity-chart')
    drawdown_html = fig_drawdown.to_html(full_html=False, include_plotlyjs=False, div_id='drawdown-chart')
    monthly_html = fig_monthly.to_html(full_html=False, include_plotlyjs=False, div_id='monthly-chart')
    dist_html = fig_dist.to_html(full_html=False, include_plotlyjs=False, div_id='dist-chart')
    pie_html = fig_pie.to_html(full_html=False, include_plotlyjs=False, div_id='pie-chart')
    waterfall_html = fig_waterfall.to_html(full_html=False, include_plotlyjs=False, div_id='waterfall-chart')
    monthly_cum_html = fig_monthly_cum.to_html(full_html=False, include_plotlyjs=False, div_id='monthly-cum-chart')
    
    # ==========================================
    # Build trade table HTML
    # ==========================================
    
    trade_rows = ""
    for _, row in df.iterrows():
        pnl = row['Net PnL']
        pnl_class = "positive" if pnl > 0 else "negative"
        sl_badges = ""
        if row.get('Call SL Hit', False):
            sl_badges += '<span class="badge badge-red">Call SL</span> '
        if row.get('Put SL Hit', False):
            sl_badges += '<span class="badge badge-amber">Put SL</span> '
        
        trade_rows += f"""
        <tr class="trade-row {pnl_class}">
            <td>{int(row['Trade #'])}</td>
            <td>{row['Entry Time'].strftime('%Y-%m-%d %H:%M')}</td>
            <td>{row['Exit Time'].strftime('%Y-%m-%d %H:%M')}</td>
            <td><span class="badge badge-{'cyan' if row['Exit Reason']=='Expiry' else 'red'}">{row['Exit Reason']}</span></td>
            <td>${row['Underlying Entry Price']:,.0f}</td>
            <td>${row['Call Strike']:,.0f} / ${row['Put Strike']:,.0f}</td>
            <td>${row['Total Premium Collected']:,.2f}</td>
            <td>${row['Total Slippage Paid']:,.2f}</td>
            <td class="{pnl_class}">${pnl:,.2f}</td>
        </tr>"""
    
    # ==========================================
    # Build monthly table HTML
    # ==========================================
    
    monthly_rows = ""
    for _, row in monthly.iterrows():
        pnl_class = "positive" if row['net_pnl'] > 0 else "negative"
        monthly_rows += f"""
        <tr>
            <td><strong>{row['Month']}</strong></td>
            <td>{int(row['trades'])}</td>
            <td>{row['win_rate']:.1f}%</td>
            <td class="{pnl_class}">${row['net_pnl']:,.2f}</td>
            <td>${row['avg_pnl']:,.2f}</td>
            <td class="positive">${row['best_trade']:,.2f}</td>
            <td class="negative">${row['worst_trade']:,.2f}</td>
            <td>${row['premium_collected']:,.2f}</td>
            <td>${row['total_slippage']:,.2f}</td>
            <td class="{('positive' if row['cumulative_pnl'] > 0 else 'negative')}">${row['cumulative_pnl']:,.2f}</td>
        </tr>"""
    
    # ==========================================
    # Metric card helper
    # ==========================================
    
    m = metrics
    pnl_color = GREEN if m['cum_pnl'] > 0 else RED
    
    # ==========================================
    # Full HTML Template
    # ==========================================
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Short Strangle Backtest Dashboard</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-primary: {BG_DARK};
            --bg-card: {BG_CARD};
            --bg-hover: #1c2129;
            --text-primary: {TEXT_BRIGHT};
            --text-secondary: {TEXT_COLOR};
            --text-muted: #8b949e;
            --border: #30363d;
            --green: {GREEN};
            --red: {RED};
            --cyan: {CYAN};
            --amber: {AMBER};
            --purple: {PURPLE};
            --gradient-cyan: linear-gradient(135deg, #00BCD4, #0097A7);
            --gradient-green: linear-gradient(135deg, #00E676, #00C853);
            --gradient-red: linear-gradient(135deg, #FF5252, #D32F2F);
            --gradient-amber: linear-gradient(135deg, #FFD740, #FFC107);
            --gradient-purple: linear-gradient(135deg, #B388FF, #7C4DFF);
        }}
        
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: var(--bg-primary);
            color: var(--text-secondary);
            line-height: 1.6;
            min-height: 100vh;
        }}
        
        .dashboard {{
            max-width: 1440px;
            margin: 0 auto;
            padding: 24px;
        }}
        
        /* Header */
        .header {{
            text-align: center;
            padding: 40px 20px 30px;
            margin-bottom: 30px;
            background: linear-gradient(135deg, rgba(0,188,212,0.08) 0%, rgba(179,136,255,0.08) 100%);
            border: 1px solid var(--border);
            border-radius: 16px;
            position: relative;
            overflow: hidden;
        }}
        .header::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--cyan), var(--purple), var(--cyan));
        }}
        .header h1 {{
            font-size: 32px;
            font-weight: 800;
            background: linear-gradient(135deg, var(--cyan), var(--purple));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 8px;
            letter-spacing: -0.5px;
        }}
        .header .subtitle {{
            font-size: 15px;
            color: var(--text-muted);
            font-weight: 400;
        }}
        .header .subtitle span {{
            color: var(--cyan);
            font-weight: 600;
        }}
        
        /* Metric Cards */
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 30px;
        }}
        .metric-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            transition: all 0.2s ease;
            position: relative;
            overflow: hidden;
        }}
        .metric-card:hover {{
            border-color: var(--cyan);
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0,188,212,0.1);
        }}
        .metric-card .label {{
            font-size: 12px;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: var(--text-muted);
            margin-bottom: 8px;
        }}
        .metric-card .value {{
            font-size: 24px;
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-primary);
        }}
        .metric-card .value.positive {{ color: var(--green); }}
        .metric-card .value.negative {{ color: var(--red); }}
        .metric-card .value.cyan {{ color: var(--cyan); }}
        .metric-card .value.amber {{ color: var(--amber); }}
        .metric-card .value.purple {{ color: var(--purple); }}
        .metric-card .accent-bar {{
            position: absolute;
            bottom: 0; left: 0; right: 0;
            height: 3px;
        }}
        
        /* Section Title */
        .section-title {{
            font-size: 20px;
            font-weight: 700;
            color: var(--text-primary);
            margin: 35px 0 18px;
            padding-left: 12px;
            border-left: 3px solid var(--cyan);
        }}
        
        /* Chart Container */
        .chart-container {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            transition: border-color 0.2s;
        }}
        .chart-container:hover {{
            border-color: rgba(0,188,212,0.3);
        }}
        
        .charts-row {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }}
        
        /* Tables */
        .table-container {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 20px;
        }}
        .table-scroll {{
            overflow-x: auto;
            max-height: 600px;
            overflow-y: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        thead {{
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        th {{
            background: #1c2129;
            color: var(--text-primary);
            font-weight: 600;
            padding: 14px 12px;
            text-align: left;
            border-bottom: 2px solid var(--border);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            white-space: nowrap;
        }}
        td {{
            padding: 10px 12px;
            border-bottom: 1px solid rgba(48,54,61,0.5);
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            white-space: nowrap;
        }}
        tr:hover td {{
            background: var(--bg-hover);
        }}
        .positive {{ color: var(--green); font-weight: 600; }}
        .negative {{ color: var(--red); font-weight: 600; }}
        
        /* Badges */
        .badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.3px;
        }}
        .badge-cyan {{ background: rgba(0,188,212,0.15); color: var(--cyan); }}
        .badge-red {{ background: rgba(255,82,82,0.15); color: var(--red); }}
        .badge-amber {{ background: rgba(255,215,64,0.15); color: var(--amber); }}
        .badge-purple {{ background: rgba(179,136,255,0.15); color: var(--purple); }}
        .badge-green {{ background: rgba(0,230,118,0.15); color: var(--green); }}
        
        /* Nav Tabs */
        .tab-nav {{
            display: flex;
            gap: 4px;
            margin-bottom: 0;
            padding: 12px 16px 0;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-bottom: none;
            border-radius: 12px 12px 0 0;
        }}
        .tab-btn {{
            padding: 10px 20px;
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-family: 'Inter', sans-serif;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            border-radius: 8px 8px 0 0;
            transition: all 0.2s;
            border-bottom: 2px solid transparent;
        }}
        .tab-btn:hover {{ color: var(--text-primary); background: var(--bg-hover); }}
        .tab-btn.active {{
            color: var(--cyan);
            border-bottom-color: var(--cyan);
            background: var(--bg-hover);
        }}
        .tab-content {{
            display: none;
            border: 1px solid var(--border);
            border-top: none;
            border-radius: 0 0 12px 12px;
        }}
        .tab-content.active {{ display: block; }}
        
        /* Footer */
        .footer {{
            text-align: center;
            padding: 30px 20px;
            color: var(--text-muted);
            font-size: 12px;
            border-top: 1px solid var(--border);
            margin-top: 40px;
        }}
        
        /* Responsive */
        @media (max-width: 768px) {{
            .metrics-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .charts-row {{ grid-template-columns: 1fr; }}
            .header h1 {{ font-size: 24px; }}
            .dashboard {{ padding: 12px; }}
        }}
        
        /* Scrollbar */
        ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
        ::-webkit-scrollbar-track {{ background: var(--bg-primary); }}
        ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 4px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: var(--text-muted); }}
        
        /* Search/Filter */
        .filter-bar {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 16px;
            background: var(--bg-card);
        }}
        .filter-input {{
            flex: 1;
            padding: 8px 14px;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            font-size: 13px;
            outline: none;
            transition: border-color 0.2s;
        }}
        .filter-input:focus {{ border-color: var(--cyan); }}
        .filter-input::placeholder {{ color: var(--text-muted); }}
    </style>
</head>
<body>
    <div class="dashboard">
        <!-- Header -->
        <div class="header">
            <h1>⚡ Short Strangle Backtest Dashboard</h1>
            <p class="subtitle">
                <span>BTCUSDT</span> &bull; 10% OTM &bull; 30% Stop-Loss &bull; 8% Slippage &bull; 
                {df['Entry Time'].min().strftime('%b %d, %Y')} → {df['Exit Time'].max().strftime('%b %d, %Y')} &bull;
                <span>{metrics['total_trades']} trades</span>
            </p>
        </div>
        
        <!-- Summary Metric Cards -->
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="label">Cumulative P&L</div>
                <div class="value {'positive' if m['cum_pnl'] > 0 else 'negative'}">${m['cum_pnl']:,.2f}</div>
                <div class="accent-bar" style="background: {'var(--gradient-green)' if m['cum_pnl'] > 0 else 'var(--gradient-red)'}"></div>
            </div>
            <div class="metric-card">
                <div class="label">Win Rate</div>
                <div class="value cyan">{m['win_rate']:.1f}%</div>
                <div class="accent-bar" style="background: var(--gradient-cyan)"></div>
            </div>
            <div class="metric-card">
                <div class="label">Total Trades</div>
                <div class="value" style="color: var(--text-primary)">{m['total_trades']}</div>
                <div class="accent-bar" style="background: var(--gradient-cyan)"></div>
            </div>
            <div class="metric-card">
                <div class="label">Profit Factor</div>
                <div class="value purple">{m['profit_factor']:.2f}</div>
                <div class="accent-bar" style="background: var(--gradient-purple)"></div>
            </div>
            <div class="metric-card">
                <div class="label">Sharpe Ratio</div>
                <div class="value amber">{m['sharpe']:.2f}</div>
                <div class="accent-bar" style="background: var(--gradient-amber)"></div>
            </div>
            <div class="metric-card">
                <div class="label">Max Drawdown</div>
                <div class="value negative">${m['max_drawdown']:,.2f}</div>
                <div class="accent-bar" style="background: var(--gradient-red)"></div>
            </div>
            <div class="metric-card">
                <div class="label">Avg Win</div>
                <div class="value positive">${m['avg_win']:,.2f}</div>
                <div class="accent-bar" style="background: var(--gradient-green)"></div>
            </div>
            <div class="metric-card">
                <div class="label">Avg Loss</div>
                <div class="value negative">${m['avg_loss']:,.2f}</div>
                <div class="accent-bar" style="background: var(--gradient-red)"></div>
            </div>
            <div class="metric-card">
                <div class="label">Best Trade</div>
                <div class="value positive">${m['best_trade']:,.2f}</div>
                <div class="accent-bar" style="background: var(--gradient-green)"></div>
            </div>
            <div class="metric-card">
                <div class="label">Worst Trade</div>
                <div class="value negative">${m['worst_trade']:,.2f}</div>
                <div class="accent-bar" style="background: var(--gradient-red)"></div>
            </div>
            <div class="metric-card">
                <div class="label">Expectancy</div>
                <div class="value {'positive' if m['expectancy'] > 0 else 'negative'}">${m['expectancy']:,.2f}</div>
                <div class="accent-bar" style="background: {'var(--gradient-green)' if m['expectancy'] > 0 else 'var(--gradient-red)'}"></div>
            </div>
            <div class="metric-card">
                <div class="label">Total Slippage</div>
                <div class="value negative">${m['total_slippage']:,.2f}</div>
                <div class="accent-bar" style="background: var(--gradient-red)"></div>
            </div>
        </div>
        
        <!-- Secondary Metrics Row -->
        <div class="metrics-grid" style="grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); margin-bottom: 30px;">
            <div class="metric-card">
                <div class="label">Wins</div>
                <div class="value positive">{m['wins']}</div>
            </div>
            <div class="metric-card">
                <div class="label">Losses</div>
                <div class="value negative">{m['losses']}</div>
            </div>
            <div class="metric-card">
                <div class="label">Max Win Streak</div>
                <div class="value positive">{m['max_win_streak']}</div>
            </div>
            <div class="metric-card">
                <div class="label">Max Loss Streak</div>
                <div class="value negative">{m['max_loss_streak']}</div>
            </div>
            <div class="metric-card">
                <div class="label">Gross Profit</div>
                <div class="value positive">${m['gross_profit']:,.2f}</div>
            </div>
            <div class="metric-card">
                <div class="label">Gross Loss</div>
                <div class="value negative">${m['gross_loss']:,.2f}</div>
            </div>
            <div class="metric-card">
                <div class="label">Avg P&L / Trade</div>
                <div class="value {'positive' if m['avg_pnl'] > 0 else 'negative'}">${m['avg_pnl']:,.2f}</div>
            </div>
            <div class="metric-card">
                <div class="label">Total Premium</div>
                <div class="value cyan">${m['total_premium']:,.2f}</div>
            </div>
        </div>
        
        <!-- Equity Curve -->
        <h2 class="section-title">Performance Charts</h2>
        <div class="chart-container">
            {equity_html}
        </div>
        
        <!-- Drawdown + Distribution -->
        <div class="charts-row">
            <div class="chart-container">
                {drawdown_html}
            </div>
            <div class="chart-container">
                {dist_html}
            </div>
        </div>
        
        <!-- Monthly P&L -->
        <h2 class="section-title">Month-on-Month Analysis</h2>
        <div class="chart-container">
            {monthly_html}
        </div>
        
        <div class="charts-row">
            <div class="chart-container">
                {monthly_cum_html}
            </div>
            <div class="chart-container">
                {pie_html}
            </div>
        </div>
        
        <!-- Monthly Detail Table -->
        <div class="table-container">
            <div class="table-scroll">
                <table>
                    <thead>
                        <tr>
                            <th>Month</th>
                            <th>Trades</th>
                            <th>Win Rate</th>
                            <th>Net P&L</th>
                            <th>Avg P&L</th>
                            <th>Best Trade</th>
                            <th>Worst Trade</th>
                            <th>Premium Collected</th>
                            <th>Slippage Paid</th>
                            <th>Cumulative P&L</th>
                        </tr>
                    </thead>
                    <tbody>
                        {monthly_rows}
                    </tbody>
                </table>
            </div>
        </div>
        
        <!-- Per-Trade Breakdown -->
        <h2 class="section-title">Trade-by-Trade Breakdown</h2>
        
        <div class="chart-container">
            {waterfall_html}
        </div>
        
        <!-- Tab Navigation for Trade Table -->
        <div class="tab-nav">
            <button class="tab-btn active" onclick="showTab('all')">All Trades ({m['total_trades']})</button>
            <button class="tab-btn" onclick="showTab('winners')">Winners ({m['wins']})</button>
            <button class="tab-btn" onclick="showTab('losers')">Losers ({m['losses']})</button>
        </div>
        
        <div id="tab-all" class="tab-content active">
            <div class="filter-bar">
                <input type="text" class="filter-input" placeholder="🔍 Search trades by date, exit reason, strike..." onkeyup="filterTable(this, 'all')">
            </div>
            <div class="table-scroll">
                <table id="trade-table-all">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Entry Time</th>
                            <th>Exit Time</th>
                            <th>Exit Reason</th>
                            <th>Entry Price</th>
                            <th>Strikes (C/P)</th>
                            <th>Premium</th>
                            <th>Slippage</th>
                            <th>Net P&L</th>
                        </tr>
                    </thead>
                    <tbody>
                        {trade_rows}
                    </tbody>
                </table>
            </div>
        </div>
        
        <div id="tab-winners" class="tab-content">
            <div class="table-scroll">
                <table id="trade-table-winners">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Entry Time</th>
                            <th>Exit Time</th>
                            <th>Exit Reason</th>
                            <th>Entry Price</th>
                            <th>Strikes (C/P)</th>
                            <th>Premium</th>
                            <th>Slippage</th>
                            <th>Net P&L</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(f"""
                        <tr class="trade-row positive">
                            <td>{int(row['Trade #'])}</td>
                            <td>{row['Entry Time'].strftime('%Y-%m-%d %H:%M')}</td>
                            <td>{row['Exit Time'].strftime('%Y-%m-%d %H:%M')}</td>
                            <td><span class="badge badge-green">{row['Exit Reason']}</span></td>
                            <td>${row['Underlying Entry Price']:,.0f}</td>
                            <td>${row['Call Strike']:,.0f} / ${row['Put Strike']:,.0f}</td>
                            <td>${row['Total Premium Collected']:,.2f}</td>
                            <td>${row['Total Slippage Paid']:,.2f}</td>
                            <td class="positive">${row['Net PnL']:,.2f}</td>
                        </tr>""" for _, row in df[df['Win']].iterrows())}
                    </tbody>
                </table>
            </div>
        </div>
        
        <div id="tab-losers" class="tab-content">
            <div class="table-scroll">
                <table id="trade-table-losers">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Entry Time</th>
                            <th>Exit Time</th>
                            <th>Exit Reason</th>
                            <th>Entry Price</th>
                            <th>Strikes (C/P)</th>
                            <th>Premium</th>
                            <th>Slippage</th>
                            <th>Net P&L</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(f"""
                        <tr class="trade-row negative">
                            <td>{int(row['Trade #'])}</td>
                            <td>{row['Entry Time'].strftime('%Y-%m-%d %H:%M')}</td>
                            <td>{row['Exit Time'].strftime('%Y-%m-%d %H:%M')}</td>
                            <td><span class="badge badge-red">{row['Exit Reason']}</span></td>
                            <td>${row['Underlying Entry Price']:,.0f}</td>
                            <td>${row['Call Strike']:,.0f} / ${row['Put Strike']:,.0f}</td>
                            <td>${row['Total Premium Collected']:,.2f}</td>
                            <td>${row['Total Slippage Paid']:,.2f}</td>
                            <td class="negative">${row['Net PnL']:,.2f}</td>
                        </tr>""" for _, row in df[~df['Win']].iterrows())}
                    </tbody>
                </table>
            </div>
        </div>
        
        <!-- Strategy Parameters Summary -->
        <h2 class="section-title">Strategy Parameters</h2>
        <div class="metrics-grid" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
            <div class="metric-card">
                <div class="label">OTM Distance</div>
                <div class="value cyan">10%</div>
            </div>
            <div class="metric-card">
                <div class="label">Stop Loss</div>
                <div class="value amber">30%</div>
            </div>
            <div class="metric-card">
                <div class="label">Slippage Model</div>
                <div class="value" style="color: var(--text-primary); font-size: 18px;">8% of premium</div>
            </div>
            <div class="metric-card">
                <div class="label">Trade Frequency</div>
                <div class="value purple">Weekly</div>
            </div>
            <div class="metric-card">
                <div class="label">Implied Volatility</div>
                <div class="value cyan">60%</div>
            </div>
            <div class="metric-card">
                <div class="label">Taker Fee</div>
                <div class="value" style="color: var(--text-primary); font-size: 18px;">3 bps</div>
            </div>
        </div>
        
        <!-- Footer -->
        <div class="footer">
            <p>Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} &bull; 
               Data: Delta Exchange BTCUSDT &bull; 
               Strategy: Short Strangle (AlgoTest Classic)</p>
            <p style="margin-top: 8px; color: var(--text-muted);">
                ⚠️ Past performance does not guarantee future results. This backtest uses Black-Scholes pricing with constant 60% IV, 
                which may differ from actual market conditions. Always validate with live paper trading before deploying capital.
            </p>
        </div>
    </div>
    
    <script>
        // Tab switching
        function showTab(tab) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById('tab-' + tab).classList.add('active');
            event.target.classList.add('active');
        }}
        
        // Table search/filter
        function filterTable(input, tabId) {{
            const filter = input.value.toLowerCase();
            const table = document.getElementById('trade-table-' + tabId);
            const rows = table.getElementsByTagName('tr');
            for (let i = 1; i < rows.length; i++) {{
                const cells = rows[i].getElementsByTagName('td');
                let match = false;
                for (let j = 0; j < cells.length; j++) {{
                    if (cells[j].textContent.toLowerCase().includes(filter)) {{
                        match = true;
                        break;
                    }}
                }}
                rows[i].style.display = match ? '' : 'none';
            }}
        }}
        
        // Resize Plotly charts to fit containers
        window.addEventListener('resize', function() {{
            document.querySelectorAll('.js-plotly-plot').forEach(function(plot) {{
                Plotly.Plots.resize(plot);
            }});
        }});
        
        // Initial resize after load
        window.addEventListener('load', function() {{
            setTimeout(function() {{
                document.querySelectorAll('.js-plotly-plot').forEach(function(plot) {{
                    Plotly.Plots.resize(plot);
                }});
            }}, 100);
        }});
    </script>
</body>
</html>"""
    
    # Write to file
    output_path = os.path.join(os.path.dirname(csv_path), "backtest_dashboard.html")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"Dashboard saved to: {output_path}")
    return output_path


if __name__ == "__main__":
    csv = sys.argv[1] if len(sys.argv) > 1 else None
    path = generate_dashboard(csv)
    if path:
        import webbrowser
        webbrowser.open(f"file:///{path}")
        print("Dashboard opened in browser!")
