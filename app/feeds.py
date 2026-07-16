"""Feed source list and domain→display-name map (from RSS Fetcher v4)."""
from __future__ import annotations

SOURCES: list[str] = [

    # Ethereum / Core Ecosystem
    "https://nitter.net/ethereum/rss",
    "https://nitter.net/ethereumfndn/rss",
    "https://nitter.net/VitalikButerin/rss",
    "https://nitter.net/timbeiko/rss",
    "https://nitter.net/sassal0x/rss",
    "https://nitter.net/l2beat/rss",
    "https://nitter.net/growthepie_eth/rss",
    "https://nitter.net/routescan_io/rss",
    "https://nitter.net/etherscan/rss",
    # Analytics / Data / On-Chain Intelligence
    "https://nitter.net/DefiLlama/rss",
    "https://nitter.net/tokenterminal/rss",
    "https://nitter.net/dune/rss",
    "https://nitter.net/nansen_ai/rss",
    "https://nitter.net/arkham/rss",
    "https://nitter.net/glassnode/rss",
    "https://nitter.net/coingecko/rss",
    "https://nitter.net/coinmarketcap/rss",
    "https://nitter.net/KaikoData/rss",
    "https://nitter.net/lookonchain/rss",
    "https://nitter.net/EmberCN/rss",
    "https://nitter.net/peckshield/rss",
    "https://nitter.net/SlowMist_Team/rss",

    # Research / Infra / Macro
    "https://nitter.net/hasufl/rss",
    "https://nitter.net/MessariCrypto/rss",
    "https://nitter.net/Blockworks/rss",
    "https://nitter.net/TheBlockCo/rss",
    "https://nitter.net/laurashin/rss",
    "https://nitter.net/therollupco/rss",
    "https://nitter.net/0xKofi/rss",
    "https://nitter.net/BitMEXResearch/rss",

    # DeFi / Governance / Risk
    "https://nitter.net/0xngmi/rss",
    "https://nitter.net/thedefiedge/rss",
    "https://nitter.net/DefiIgnas/rss",
    "https://nitter.net/TheDeFinvestor/rss",
    "https://nitter.net/stacy_muur/rss",
    "https://nitter.net/binji_x/rss",
    "https://nitter.net/ETH_Daily/rss",
    "https://nitter.net/SnapshotLabs/rss",
    "https://nitter.net/gauntlet_xyz/rss",
    "https://nitter.net/StaniKulechov/rss",
    "https://nitter.net/LlamaRisk/rss",
    "https://nitter.net/StableLab/rss",
    "https://nitter.net/SEEDGov/rss",

    # Layer 2 / Modular
    "https://nitter.net/base/rss",
    "https://nitter.net/Optimism/rss",
    "https://nitter.net/Arbitrum/rss",
    "https://nitter.net/LineaBuild/rss",
    "https://nitter.net/inkonchain/rss",
    "https://nitter.net/LayerZero_Core/rss",

    # Protocols
    "https://nitter.net/aave/rss",
    "https://nitter.net/Uniswap/rss",
    "https://nitter.net/Morpho/rss",
    "https://nitter.net/ethena/rss",
    "https://nitter.net/LidoFinance/rss",
    "https://nitter.net/WrappedBTC/rss",
    "https://nitter.net/jumperapp/rss",
    "https://nitter.net/wormhole/rss",
    "https://nitter.net/Lombard_Finance/rss",
    "https://nitter.net/rainbowdotme/rss",
    "https://nitter.net/lifiprotocol/rss",

    # Lending / Credit
    "https://nitter.net/SkyEcosystem/rss",
    "https://nitter.net/sparkdotfi/rss",
    "https://nitter.net/MapleFinance/rss",

    # Yield / Rates
    "https://nitter.net/pendle_fi/rss",

    # Restaking
    "https://nitter.net/SymbioticFi/rss",
    "https://nitter.net/ether_fi/rss",

    # DEXs
    "https://nitter.net/CurveFinance/rss",
    "https://nitter.net/Balancer/rss",
    "https://nitter.net/AerodromeFi/rss",

    # Perps / Trading
    "https://nitter.net/HyperliquidX/rss",

    # Stablecoins / RWA
    "https://nitter.net/circle/rss",
    "https://nitter.net/OndoFinance/rss",
    "https://nitter.net/tether/rss",

    # Infrastructure / Oracles
    "https://nitter.net/chainlink/rss",
    "https://nitter.net/PythNetwork/rss",

    # Funds / VCs / Market Structure
    "https://nitter.net/matthuang/rss",
    "https://nitter.net/paraficapital/rss",
    "https://nitter.net/JasonYanowitz/rss",
    "https://nitter.net/a16zcrypto/rss",
    "https://nitter.net/ForesightVen/rss",
    "https://nitter.net/CryptoRank_VCs/rss",
    "https://nitter.net/jump_/rss",
    "https://nitter.net/wintermute_t/rss",
    "https://nitter.net/Delphi_Digital/rss",
    "https://nitter.net/dragonfly_xyz/rss",
    "https://nitter.net/VariantFund/rss",
    "https://nitter.net/coinfund/rss",
    "https://nitter.net/PanteraCapital/rss",
    "https://nitter.net/glxyresearch/rss",
    "https://nitter.net/brian_armstrong/rss",
    "https://nitter.net/Kairos_Res/rss",
    "https://nitter.net/paradigm/rss",
    "https://nitter.net/Etherealize_io/rss",

    # News / Media
    "https://nitter.net/WuBlockchain/rss",
    "https://nitter.net/WatcherGuru/rss",
    "https://nitter.net/CoinDesk/rss",
    "https://nitter.net/DecryptMedia/rss",
    "https://nitter.net/DefiantNews/rss",
    "https://nitter.net/beincrypto/rss",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml",
    "https://blockworks.co/feed",
    # cryptobriefing.com removed 2026-06-19: its feed became a general-news/AI-slop
    # aggregator (World Cup, geopolitics, SpaceX) reframed as "...crypto impact", which
    # slips past any keyword filter. Was 26.8% of the corpus and polluted entity
    # extraction / narratives / recent-mentions. Do NOT re-add without a relevance gate.
    # Moved from insights.glassnode.com/rss/ (2026-07-07): the old host started 403-ing the
    # fetcher behind Cloudflare after Glassnode relocated the blog — 2,513 consecutive
    # failures before anyone actioned the chronic-failure log line. Same publication.
    "https://research.glassnode.com/rss/",
    "https://www.bankless.com/feed",
    "https://cointelegraph.com/rss",
    "https://cointelegraph.com/rss/tag/business",
    "https://cointelegraph.com/rss/tag/ethereum",
    "https://cointelegraph.com/rss/tag/defi",
    "https://cointelegraph.com/rss/tag/blockchain",
    "https://www.chainalysis.com/blog/feed/",

    # Exchanges
    "https://nitter.net/coinbase/rss",
    "https://nitter.net/binance/rss",
    "https://nitter.net/krakenfx/rss",

    # Optional / Experimental
    "https://nitter.net/0xNairolf/rss",
    "https://nitter.net/DromosLabs/rss",
    "https://nitter.net/arc/rss",
    "https://nitter.net/aixbt_agent/rss",
    "https://nitter.net/sui414/rss",
    "https://nitter.net/cburniske/rss",
    "https://nitter.net/SergeyNazarov/rss",
    "https://nitter.net/haydenzadams/rss",
    "https://nitter.net/cdixon/rss",
    "https://nitter.net/RuneKek/rss",
    "https://nitter.net/ASvanevik/rss",
    "https://nitter.net/lawmaster/rss",
    "https://nitter.net/fintechfrank/rss",
]

FEED_NAMES: dict[str, str] = {
    "theblock.co": "The Block",
    "decrypt.co": "Decrypt",
    "bankless.com": "Bankless",
    "rekt.news": "Rekt News",
    "weekinethereumnews.com": "Week In Ethereum",
    "blog.defillama.com": "DeFiLlama Blog",
    "chainalysis.com": "Chainalysis",
    # Kaiko has no RSS — app/kaiko.py scrapes its sitemaps and routes items through
    # ingest.clean_items, which labels non-twitter links from this map.
    "kaiko.com": "Kaiko",
}
