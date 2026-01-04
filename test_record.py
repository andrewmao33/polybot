"""
Simple script to record market data for backtesting.
Run this for a few hours to collect data.
"""
import asyncio
import logging
import signal
import sys
from ingestion.orchestrator import IngestionOrchestrator
from state.market_state import MarketState
from state.position_state import PositionState
from data.recorder import DataRecorder

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global recorder
recorder: DataRecorder = None


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    logger.info("\n🛑 Received interrupt signal, stopping recording...")
    if recorder and recorder.recording:
        recorder.stop_recording()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


def on_market_state_update(state: MarketState):
    """Callback when market state updates - record it."""
    if recorder and recorder.recording:
        recorder.record_market_state(state)


def on_position_state_reset(new_position_state: PositionState):
    """Callback when position state resets (new market)."""
    logger.info(f"New market detected: {new_position_state.market_id}")


async def main():
    """Main function to run recording."""
    global recorder
    
    logger.info("="*60)
    logger.info("📹 MARKET DATA RECORDER")
    logger.info("="*60)
    logger.info("This will record market state snapshots")
    logger.info("Press Ctrl+C to stop recording\n")
    
    # Create recorder
    recorder = DataRecorder()
    
    # Create orchestrator
    orchestrator = IngestionOrchestrator(
        on_market_state_update=on_market_state_update,
        on_position_state_reset=on_position_state_reset
    )
    
    try:
        # Initialize and get first market
        await orchestrator.initialize()
        
        if orchestrator.market_state:
            # Start recording
            session_id = recorder.start_recording(orchestrator.market_state.market_id)
            logger.info(f"✅ Recording started: {session_id}")
            logger.info(f"📊 Recording market: {orchestrator.market_state.market_id}")
            logger.info(f"📁 Data will be saved to: recorded_data/{session_id}/\n")
        
        # Start WebSocket connections (this runs until interrupted)
        logger.info("Connecting to WebSockets...")
        await orchestrator.start()
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Error during recording: {e}", exc_info=True)
    finally:
        if recorder and recorder.recording:
            recorder.stop_recording()
            logger.info("✅ Recording stopped and saved")
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())

