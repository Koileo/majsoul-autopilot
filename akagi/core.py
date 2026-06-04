import traceback
from .logger import logger


def process_messages(message_client, mjai_controller, mjai_bot, jsonl_logger=None):
    """Process MJAI messages from message_client, run inference, write logs.

    Returns:
        dict with keys: mjai_msgs, mjai_response, or None if no messages.
    """
    if not message_client.running:
        return None

    mjai_msgs = message_client.dump_messages()
    if not mjai_msgs:
        return None

    try:
        for mjai_msg in mjai_msgs:
            logger.debug(f"-> {mjai_msg}")
            if jsonl_logger:
                jsonl_logger.write_game_flow(mjai_msg)

        mjai_response = mjai_controller.react(mjai_msgs)
        logger.debug(f"<- {mjai_response}")
        mjai_bot.react(input_list=mjai_msgs)

        if jsonl_logger:
            jsonl_logger.write_inference(mjai_response, mjai_bot.tehai_mjai)

        return {
            "mjai_msgs": mjai_msgs,
            "mjai_response": mjai_response,
        }
    except Exception as e:
        logger.error(f"Error processing messages: {traceback.format_exc()}")
        return None
