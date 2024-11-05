import asyncio
import aiohttp
import ujson
import os
from datetime import datetime
from core.credentials import load_bittensor_credentials
from core.bittensor_signals import BTTSN8MinerSignal, BTTSN8Position, BTTSN8Order, BTTSN8TradePair

RAW_SIGNALS_DIR = "raw_signals/bittensor"

CORE_ASSET_MAPPING = {
    "BTCUSD": "BTCUSDT",
    "ETHUSD": "ETHUSDT"
    # Add more mappings as necessary
}

LEVERAGE_LIMIT_CRYPTO = 0.5 # we will need to differentiate between crypto and forex leverage limits if we add other types in the future


async def fetch_bittensor_signals(api_key: str, endpoint: str):
    headers = {'Content-Type': 'application/json'}
    data = {'api_key': api_key}

    async with aiohttp.ClientSession() as session:
        async with session.get(endpoint, json=data, headers=headers) as response:
            if response.status == 200:
                return await response.json(loads=ujson.loads)
            print(f"Failed to fetch data: {response.status}")
            return None

def store_signal_on_disk(data):
    if not os.path.exists(RAW_SIGNALS_DIR):
        os.makedirs(RAW_SIGNALS_DIR)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"bittensor_signal_{timestamp}.json"
    file_path = os.path.join(RAW_SIGNALS_DIR, filename)
    
    with open(file_path, 'w') as f:
        ujson.dump(data, f, indent=4)
    
    print(f"Raw signal stored at {file_path}")

def calculate_gradient_allocation(max_rank):
    """Calculate gradient allocation weights for each rank based on their inverted priority."""
    # Total weight is the sum of all rank values
    total_weight = sum(max_rank + 1 - rank for rank in range(1, max_rank + 1))
    
    # Create a dictionary with rank as key and allocation as the fractional value
    allocations = {}
    for rank in range(1, max_rank + 1):
        inverted_rank = max_rank + 1 - rank
        allocations[rank] = inverted_rank / total_weight
    return allocations

def process_signals(data, top_miners=None, mapped_only=True):
    if data is None:
        return []

    # Sort miners by all_time_returns and select the top if specified
    sorted_miners = sorted(data.items(), key=lambda x: x[1].get('all_time_returns', 0), reverse=True)
    if top_miners:
        sorted_miners = sorted_miners[:top_miners]

    # Get allocation for each miner based on rank
    allocations = calculate_gradient_allocation(len(sorted_miners))

    # Initialize asset tracking dictionaries
    asset_depths = {}
    asset_prices = {}

    # Iterate through the ranked miners and apply gradient allocations
    for rank, (miner_hotkey, miner_positions) in enumerate(sorted_miners, start=1):
        allocation_weight = allocations[rank]
        asset_tracker = set()  # Track assets seen for this miner to prevent overcounting

        for position_data in miner_positions.get('positions', []):
            original_symbol = position_data['trade_pair'][0]
            if mapped_only and original_symbol not in CORE_ASSET_MAPPING:
                continue
            symbol = CORE_ASSET_MAPPING[original_symbol]

            # Skip if this asset has already been counted for this miner
            if symbol in asset_tracker:
                continue
            asset_tracker.add(symbol)  # Mark this asset as seen for this miner

            # Calculate normalized depth based on capped leverage and allocation weight
            capped_leverage = min(position_data['net_leverage'], LEVERAGE_LIMIT_CRYPTO)
            normalized_depth = (capped_leverage / LEVERAGE_LIMIT_CRYPTO) * allocation_weight

            # Update depth and leverage-weighted price for each asset
            if symbol not in asset_depths:
                asset_depths[symbol] = 0.0
                asset_prices[symbol] = {"weighted_price_sum": 0.0, "total_depth": 0.0}
            
            asset_depths[symbol] += normalized_depth
            asset_prices[symbol]["weighted_price_sum"] += position_data['average_entry_price'] * normalized_depth
            asset_prices[symbol]["total_depth"] += normalized_depth  # Sum of normalized depths for averaging

    # Prepare final results with capped depth and weighted average price
    results = []
    for symbol, total_depth in asset_depths.items():
        capped_depth = min(total_depth, 1.0)  # Cap total depth at 1.0
        total_depth_for_price = asset_prices[symbol]["total_depth"]
        
        # Calculate weighted average price if total depth is positive
        weighted_average_price = (
            asset_prices[symbol]["weighted_price_sum"] / total_depth_for_price 
            if total_depth_for_price > 0 else 0.0
        )
        
        results.append({
            "symbol": symbol,
            "depth": capped_depth,
            "average_price": weighted_average_price
        })

    return results

async def fetch_bittensor_signal(top_miners=None):
    credentials = load_bittensor_credentials()
    api_key = credentials.bittensor_sn8.api_key
    endpoint = credentials.bittensor_sn8.endpoint
    
    positions_data = await fetch_bittensor_signals(api_key, endpoint)
    if positions_data:
        store_signal_on_disk(positions_data)
        return process_signals(positions_data, top_miners=top_miners)
    else:
        print("No data received.")
        return []

# Example standalone usage
if __name__ == '__main__':
    # return signals and print them
    signals = asyncio.run(fetch_bittensor_signal(top_miners=20))
    for signal in signals:
        print(signal)
    print(f"Total signals: {len(signals)}") 
    
