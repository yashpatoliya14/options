import time
from config import CONFIG
from exchange.delta_client import DeltaClient
from broker import Position

def main():
    print("Initializing DeltaClient...")
    client = DeltaClient(CONFIG)
    
    print(f"Fetching available expiries for {CONFIG.underlying_asset}...")
    try:
        expiries = client.get_available_expiries(CONFIG.underlying_asset)
    except Exception as e:
        print(f"Failed to fetch expiries: {e}")
        return
        
    if not expiries:
        print("No expiries found.")
        return
        
    target_expiry = expiries[0]
    print(f"Selected expiry: {target_expiry}")
    
    print("Fetching option chain...")
    try:
        quotes = client.get_option_chain(CONFIG.underlying_asset, target_expiry)
    except Exception as e:
        print(f"Failed to fetch option chain: {e}")
        return
        
    if not quotes:
        print("No quotes found for this expiry.")
        return
        
    # Find a put option with a bid price
    puts = [q for q in quotes if q.option_type == "put" and q.premium > 0]
    if not puts:
        print("No valid put options found with a premium > 0.")
        return
        
    # Pick the one with the lowest strike for minimum risk, or just the first one
    quote = sorted(puts, key=lambda x: x.strike)[0]
    print(f"Selected option: {quote.symbol} (Strike: {quote.strike}, Premium: {quote.premium})")
    
    print("Placing test SELL order (size 1)...")
    order = client.place_sell_order(quote, 1)
    print(f"Order Result: {order}")
    
    if not order.success:
        print("Failed to place demo order. Exiting.")
        return
        
    print("Sleeping for 3 seconds to let exchange process the order...")
    time.sleep(3)
    
    print("Fetching open positions...")
    positions = client.get_open_positions()
    print(f"Open positions: {positions}")
    
    target_position = None
    for p in positions:
        if p.symbol == quote.symbol:
            target_position = p
            break
            
    if target_position is None:
        print(f"WARNING: Position for {quote.symbol} not found in open positions! Creating a mock position object to attempt closing anyway.")
        target_position = Position(
            symbol=quote.symbol,
            option_type=quote.option_type,
            strike=quote.strike,
            expiry=quote.expiry,
            side="sell",
            quantity=1,
            entry_premium=order.filled_premium or quote.premium,
            entry_timestamp=int(time.time()),
            strategy_direction=""
        )
        
    print(f"Closing position: {target_position.symbol} (Size: {target_position.quantity})")
    close_order = client.close_position(target_position)
    print(f"Close Order Result: {close_order}")

if __name__ == "__main__":
    main()
