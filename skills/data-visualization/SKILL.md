---
name: data-visualization
description: "Creates effective data visualizations using various libraries and tools, with focus on clarity and insight communication. Trigger keywords: chart, graph, plot, visualization, dashboard, matplotlib, d3, plotly, visualization."
allowed-tools: Read, Grep, Glob, Edit, Write, Bash
---

# Data Visualization

## Overview

This skill focuses on creating effective data visualizations that communicate insights clearly. It covers various visualization libraries, chart selection, and design principles for impactful data presentation.

## Instructions

### 1. Understand the Data

- Analyze data structure and types
- Identify key metrics and dimensions
- Determine the story to tell
- Consider the target audience

### 2. Select Appropriate Visualization

- Match chart type to data relationship
- Consider data volume and complexity
- Plan for interactivity needs
- Account for accessibility

### 3. Design for Clarity

- Choose effective color schemes
- Label axes and data clearly
- Remove chart junk
- Highlight key insights

### 4. Implement and Iterate

- Build visualization with chosen tool
- Test with real data
- Gather feedback
- Refine based on usage

## Best Practices

1. **Right Chart for Data**: Match visualization to data type
2. **Less is More**: Remove unnecessary elements
3. **Consistent Styling**: Use coherent color schemes
4. **Accessible Design**: Consider colorblind users
5. **Clear Labels**: Descriptive titles and axis labels
6. **Context Matters**: Include reference points
7. **Interactive When Helpful**: Add tooltips and filters

## Examples

### Example 1: Python with Matplotlib/Seaborn

```python
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import numpy as np

# Set style for professional look
plt.style.use('seaborn-v0_8-whitegrid')
sns.set_palette("husl")

# Create figure with subplots
fig, axes = plt.subplots(2, 2, figsize=(14, 10))

# Example 1: Line chart for time series
df_sales = pd.DataFrame({
    'date': pd.date_range('2024-01-01', periods=12, freq='M'),
    'revenue': [100, 120, 115, 140, 155, 170, 165, 180, 195, 210, 225, 250],
    'target': [110, 115, 120, 130, 145, 160, 175, 185, 200, 215, 230, 245]
})

ax1 = axes[0, 0]
ax1.plot(df_sales['date'], df_sales['revenue'], marker='o', linewidth=2, label='Actual')
ax1.plot(df_sales['date'], df_sales['target'], linestyle='--', linewidth=2, label='Target')
ax1.fill_between(df_sales['date'], df_sales['revenue'], df_sales['target'],
                  alpha=0.3, where=(df_sales['revenue'] >= df_sales['target']), color='green')
ax1.fill_between(df_sales['date'], df_sales['revenue'], df_sales['target'],
                  alpha=0.3, where=(df_sales['revenue'] < df_sales['target']), color='red')
ax1.set_title('Monthly Revenue vs Target', fontsize=14, fontweight='bold')
ax1.set_xlabel('Month')
ax1.set_ylabel('Revenue ($K)')
ax1.legend()
ax1.tick_params(axis='x', rotation=45)

# Example 2: Bar chart for comparison
df_products = pd.DataFrame({
    'product': ['Product A', 'Product B', 'Product C', 'Product D', 'Product E'],
    'sales': [45, 32, 28, 22, 18]
})

ax2 = axes[0, 1]
colors = sns.color_palette("Blues_r", len(df_products))
bars = ax2.barh(df_products['product'], df_products['sales'], color=colors)
ax2.bar_label(bars, padding=3, fmt='$%.0fK')
ax2.set_title('Sales by Product', fontsize=14, fontweight='bold')
ax2.set_xlabel('Sales ($K)')
ax2.invert_yaxis()

# Example 3: Scatter plot with regression
np.random.seed(42)
df_scatter = pd.DataFrame({
    'ad_spend': np.random.uniform(10, 100, 50),
    'conversions': lambda x: x['ad_spend'] * 2.5 + np.random.normal(0, 15, 50)
}.__class__.__call__(pd.DataFrame({'ad_spend': np.random.uniform(10, 100, 50)})))
df_scatter['conversions'] = df_scatter['ad_spend'] * 2.5 + np.random.normal(0, 15, 50)

ax3 = axes[1, 0]
sns.regplot(data=df_scatter, x='ad_spend', y='conversions', ax=ax3,
            scatter_kws={'alpha': 0.6}, line_kws={'color': 'red'})
ax3.set_title('Ad Spend vs Conversions', fontsize=14, fontweight='bold')
ax3.set_xlabel('Ad Spend ($K)')
ax3.set_ylabel('Conversions')

# Example 4: Pie/Donut chart for composition
df_channels = pd.DataFrame({
    'channel': ['Organic', 'Paid Search', 'Social', 'Email', 'Direct'],
    'traffic': [35, 25, 20, 12, 8]
})

ax4 = axes[1, 1]
wedges, texts, autotexts = ax4.pie(
    df_channels['traffic'],
    labels=df_channels['channel'],
    autopct='%1.1f%%',
    pctdistance=0.75,
    wedgeprops=dict(width=0.5)
)
ax4.set_title('Traffic by Channel', fontsize=14, fontweight='bold')

plt.tight_layout()
plt.savefig('dashboard.png', dpi=150, bbox_inches='tight')
plt.show()
```

### Example 2: Interactive Visualization with Plotly

```python
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd

# Create interactive time series
df = pd.DataFrame({
    'date': pd.date_range('2024-01-01', periods=365, freq='D'),
    'value': (pd.Series(range(365)) * 0.1 +
              np.sin(pd.Series(range(365)) * 0.1) * 20 +
              np.random.normal(0, 5, 365)).cumsum()
})

fig = go.Figure()

fig.add_trace(go.Scatter(
    x=df['date'],
    y=df['value'],
    mode='lines',
    name='Daily Value',
    line=dict(color='#1f77b4', width=1.5),
    hovertemplate='%{x|%B %d, %Y}<br>Value: %{y:.2f}<extra></extra>'
))

# Add moving average
df['ma_7'] = df['value'].rolling(7).mean()
fig.add_trace(go.Scatter(
    x=df['date'],
    y=df['ma_7'],
    mode='lines',
    name='7-day MA',
    line=dict(color='#ff7f0e', width=2, dash='dash')
))

fig.update_layout(
    title='Daily Performance with Moving Average',
    xaxis_title='Date',
    yaxis_title='Value',
    hovermode='x unified',
    template='plotly_white',
    xaxis=dict(
        rangeselector=dict(
            buttons=list([
                dict(count=7, label="1w", step="day", stepmode="backward"),
                dict(count=1, label="1m", step="month", stepmode="backward"),
                dict(count=3, label="3m", step="month", stepmode="backward"),
                dict(step="all")
            ])
        ),
        rangeslider=dict(visible=True)
    )
)

fig.write_html('interactive_chart.html')
fig.show()
```

### Example 3: Chart Type Selection Guide

```markdown
## Chart Selection by Data Type

### Comparison

- **Bar Chart**: Compare values across categories
- **Grouped Bar**: Compare multiple series across categories
- **Bullet Chart**: Show performance against target

### Distribution

- **Histogram**: Show frequency distribution
- **Box Plot**: Show distribution summary statistics
- **Violin Plot**: Show distribution shape

### Composition

- **Pie/Donut Chart**: Show parts of a whole (< 6 categories)
- **Stacked Bar**: Show composition across categories
- **Treemap**: Show hierarchical composition

### Relationship

- **Scatter Plot**: Show correlation between two variables
- **Bubble Chart**: Add third dimension via size
- **Heatmap**: Show correlation matrix

### Time Series

- **Line Chart**: Show trends over time
- **Area Chart**: Show cumulative trends
- **Candlestick**: Show OHLC financial data

### Geographic

- **Choropleth**: Show values by region
- **Point Map**: Show locations with values
- **Flow Map**: Show movement between locations
```

### Example 4: Dashboard Layout Principles

```python
# Streamlit Dashboard Example
import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Sales Dashboard", layout="wide")

# Header
st.title("Sales Performance Dashboard")
st.markdown("---")

# KPI Row
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Revenue", "$1.2M", "+12%")
with col2:
    st.metric("Orders", "8,543", "+8%")
with col3:
    st.metric("Avg Order Value", "$140", "+3%")
with col4:
    st.metric("Conversion Rate", "3.2%", "-0.5%")

st.markdown("---")

# Filters
with st.sidebar:
    st.header("Filters")
    date_range = st.date_input("Date Range", [])
    region = st.multiselect("Region", ["North", "South", "East", "West"])
    category = st.selectbox("Category", ["All", "Electronics", "Clothing", "Home"])

# Main Charts
left_col, right_col = st.columns([2, 1])

with left_col:
    st.subheader("Revenue Trend")
    # Line chart here

with right_col:
    st.subheader("Sales by Region")
    # Pie chart here

# Detail Table
st.subheader("Recent Orders")
# Data table here
```
