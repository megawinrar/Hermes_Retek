---
name: data-science
version: 1.0.0
description: "Data Science toolkit: EDA, visualization, ML, Kaggle competitions, statistical analysis."
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [data-science, kaggle, ml, statistics, visualization, pandas]
    related_skills: [jupyter-live-kernel, excel-analyst, ocr-verify]
---

# Data Science & Kaggle Toolkit

## Environment

Use the Jupyter kernel for interactive analysis:
```
Start: jupyter kernel in the background
```

## 1. Exploratory Data Analysis (EDA)

```python
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# Load data
train = pd.read_csv('train.csv')
test = pd.read_csv('test.csv')

# EDA Template
def full_eda(df):
    print(f"Shape: {df.shape}")
    print(f"\nMissing values:\n{df.isnull().sum()[df.isnull().sum() > 0]}")
    print(f"\nDuplicates: {df.duplicated().sum()}")
    print(f"\nTypes:\n{df.dtypes.value_counts()}")

    # Numeric columns
    numeric = df.select_dtypes(include=[np.number])
    if len(numeric.columns) > 0:
        print(f"\nNumeric summary:\n{numeric.describe()}")

    return numeric

numeric_cols = full_eda(train)
```

## 2. Visualization Suite

```python
# Distribution plots
fig, axes = plt.subplots(2, 3, figsize=(18, 10))
for idx, col in enumerate(numeric_cols.columns[:6]):
    ax = axes[idx // 3, idx % 3]
    sns.histplot(train[col], kde=True, ax=ax)
    ax.set_title(f'Distribution: {col}')
plt.tight_layout()
plt.savefig('distributions.png', dpi=150)

# Correlation matrix
plt.figure(figsize=(14, 10))
mask = np.triu(np.ones_like(train.corr(), dtype=bool))
sns.heatmap(train.corr(), mask=mask, annot=True, cmap='RdYlBu_r', center=0, fmt='.2f')
plt.title('Feature Correlations')
plt.savefig('correlation_matrix.png', dpi=150, bbox_inches='tight')

# Target analysis
plt.figure(figsize=(10, 6))
if train['target'].nunique() <= 10:
    # Classification
    sns.countplot(data=train, x='target')
else:
    # Regression
    sns.histplot(train['target'], kde=True)
plt.title('Target Distribution')
plt.savefig('target_dist.png', dpi=150)
```

## 3. Feature Engineering

```python
# Common transformations
def engineer_features(df):
    # Log transform for skewed data
    df['log_income'] = np.log1p(df['income'])

    # Binning
    df['age_group'] = pd.cut(df['age'], bins=[0, 18, 30, 45, 60, 100], labels=['child', 'young', 'middle', 'senior', 'elderly'])

    # Interaction features
    df['income_per_family'] = df['income'] / (df['family_size'] + 1)

    # Date features
    df['year'] = pd.to_datetime(df['date']).dt.year
    df['month'] = pd.to_datetime(df['date']).dt.month
    df['dayofweek'] = pd.to_datetime(df['date']).dt.dayofweek

    # Encoding
    df = pd.get_dummies(df, columns=['category'], drop_first=True)

    return df

train = engineer_features(train)
```

## 4. ML Pipeline

```python
from sklearn.model_selection import train_test_split, cross_val_score, GridSearchCV
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

# Prepare data
X = train.drop('target', axis=1)
y = train['target']
X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

# Scale
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_val_scaled = scaler.transform(X_val)

# Train models
models = {
    'Random Forest': RandomForestClassifier(n_estimators=100, random_state=42),
    'Gradient Boosting': GradientBoostingClassifier(random_state=42),
    'Logistic Regression': LogisticRegression(max_iter=1000, random_state=42)
}

results = {}
for name, model in models.items():
    model.fit(X_train_scaled, y_train)
    pred = model.predict(X_val_scaled)
    acc = accuracy_score(y_val, pred)
    results[name] = acc
    print(f"{name}: {acc:.4f}")

# Best model
best = max(results, key=results.get)
print(f"\n🏆 Best: {best} ({results[best]:.4f})")
```

## 5. Kaggle Submission

```python
# Generate predictions
best_model = models[best]
test_scaled = scaler.transform(test)
predictions = best_model.predict(test_scaled)

# Create submission
submission = pd.DataFrame({
    'id': test['id'],
    'target': predictions
})
submission.to_csv('submission.csv', index=False)
print("Submission saved!")
```

## 6. Statistical Analysis

```python
from scipy import stats

# T-test
group_a = train[train['category'] == 'A']['value']
group_b = train[train['category'] == 'B']['value']
t_stat, p_value = stats.ttest_ind(group_a, group_b)
print(f"T-test: t={t_stat:.3f}, p={p_value:.4f}")

# Chi-square
table = pd.crosstab(train['cat1'], train['cat2'])
chi2, p, dof, expected = stats.chi2_contingency(table)
print(f"Chi-square: {chi2:.3f}, p={p:.4f}")
```

## Export Charts for Telegram

Always save as PNG for sharing:
```python
plt.savefig('/opt/data/workspace/chart.png', dpi=150, bbox_inches='tight')
```
