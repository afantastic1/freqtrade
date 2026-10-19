# Fundamental PIT Eligibility Audit

- Audit version: `pit-eligibility-audit-v1`
- Window: `2022-01-01` to `2025-01-01` (exclusive)
- Datasets: 14; metric series: 30
- Strict eligible datasets: 0
- Strict eligibility is granted only after every sidecar row and evidence blob validates.

| Dataset | Source | Metrics | PIT | Strict eligible | Missing evidence |
|---|---|---|---|---|---|
| `bitcoin_chain_tvl_dev` | `defillama-chain-tvl` | chain_tvl_usd | UNVERIFIED | no | historical publication timestamps or point-in-time API vintages; revision identifiers bound to each historical TVL value; evidence proving a conservative availability upper bound |
| `bsc_chain_tvl_dev` | `defillama-chain-tvl` | chain_tvl_usd | UNVERIFIED | no | historical publication timestamps or point-in-time API vintages; revision identifiers bound to each historical TVL value; evidence proving a conservative availability upper bound |
| `coinmetrics_bnb_dev_v1` | `coinmetrics-community` | CapMrktEstUSD | UNVERIFIED | no | source publication timestamps or archived release records per observation; historical source vintages proving the value was available at that time; source revision identifiers bound to each observation |
| `coinmetrics_btc_dev_v1` | `coinmetrics-community` | AdrActCnt, CapMVRVCur, CapMrktCurUSD, CapMrktEstUSD, HashRate, SplyCur, TxCnt | UNVERIFIED | no | source publication timestamps or archived release records per observation; historical source vintages proving the value was available at that time; source revision identifiers bound to each observation |
| `coinmetrics_eth_dev_v1` | `coinmetrics-community` | AdrActCnt, CapMVRVCur, CapMrktCurUSD, CapMrktEstUSD, SplyCur, TxCnt | UNVERIFIED | no | source publication timestamps or archived release records per observation; historical source vintages proving the value was available at that time; source revision identifiers bound to each observation |
| `coinmetrics_sol_dev_v1` | `coinmetrics-community` | CapMrktEstUSD | UNVERIFIED | no | source publication timestamps or archived release records per observation; historical source vintages proving the value was available at that time; source revision identifiers bound to each observation |
| `coinmetrics_xrp_dev_v1` | `coinmetrics-community` | AdrActCnt, CapMVRVCur, CapMrktCurUSD, CapMrktEstUSD, SplyCur, TxCnt | UNVERIFIED | no | source publication timestamps or archived release records per observation; historical source vintages proving the value was available at that time; source revision identifiers bound to each observation |
| `defillama_bsc_dailyfees_dev_v1` | `defillama-chain-fees-overview` | ecosystem_daily_fees_usd | UNVERIFIED | no | historical publication timestamps or point-in-time API vintages; revision identifiers bound to each historical fee/revenue value; evidence proving a conservative availability upper bound |
| `defillama_bsc_dailyrevenue_dev_v1` | `defillama-chain-fees-overview` | ecosystem_daily_revenue_usd | UNVERIFIED | no | historical publication timestamps or point-in-time API vintages; revision identifiers bound to each historical fee/revenue value; evidence proving a conservative availability upper bound |
| `defillama_solana_dailyfees_dev_v1` | `defillama-chain-fees-overview` | ecosystem_daily_fees_usd | UNVERIFIED | no | historical publication timestamps or point-in-time API vintages; revision identifiers bound to each historical fee/revenue value; evidence proving a conservative availability upper bound |
| `defillama_solana_dailyrevenue_dev_v1` | `defillama-chain-fees-overview` | ecosystem_daily_revenue_usd | UNVERIFIED | no | historical publication timestamps or point-in-time API vintages; revision identifiers bound to each historical fee/revenue value; evidence proving a conservative availability upper bound |
| `ethereum_chain_tvl_dev` | `defillama-chain-tvl` | chain_tvl_usd | UNVERIFIED | no | historical publication timestamps or point-in-time API vintages; revision identifiers bound to each historical TVL value; evidence proving a conservative availability upper bound |
| `solana_chain_tvl_dev` | `defillama-chain-tvl` | chain_tvl_usd | UNVERIFIED | no | historical publication timestamps or point-in-time API vintages; revision identifiers bound to each historical TVL value; evidence proving a conservative availability upper bound |
| `xrpl_chain_tvl_dev` | `defillama-chain-tvl` | chain_tvl_usd | UNVERIFIED | no | historical publication timestamps or point-in-time API vintages; revision identifiers bound to each historical TVL value; evidence proving a conservative availability upper bound |
