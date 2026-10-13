"""Original Supertrend signal calculation."""
import pandas as pd
import pandas_ta as ta  # Registers DataFrame.ta.

class SignalGenerator:
    @staticmethod
    def calculate_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3) -> pd.DataFrame:
        if not all(col in df.columns for col in ["high", "low", "close"]):
            raise ValueError("DataFrame must include high, low, and close columns")
            
        df.ta.supertrend(length=period, multiplier=multiplier, append=True)
        # pandas-ta releases use either 3 or 3.0 in column suffixes.
        # Support both without changing the indicator calculation or parameters.
        rename_map = {
            f"{prefix}_{period}_{suffix}": name
            for prefix, name in [("SUPERT", "supertrend"),
                                 ("SUPERTd", "direction_signal"),
                                 ("SUPERTl", "final_lower_band"),
                                 ("SUPERTs", "final_upper_band")]
            for suffix in {str(multiplier), str(float(multiplier))}
        }
        return df.rename(columns=rename_map)

