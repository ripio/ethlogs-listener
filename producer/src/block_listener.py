import os
import logging
import time
import pika
import redis
from utils import get_last_block_number
from utils import get_last_block_processed

logger = logging.getLogger("block_listener")

SLEEP_NEW_BLOCKS = int(os.environ.get("SLEEP_NEW_BLOCKS", 5))
SLEEP_SEC_QUEUE_FULL = int(os.environ.get("SLEEP_SEC_QUEUE_FULL", 30))
MIN_QUEUE_SIZE = int(os.environ.get("MIN_QUEUE_SIZE", 25))
SYNC_FROM_BLOCK = int(os.environ.get("SYNC_FROM_BLOCK", 0))


class BlockListener():
    def __init__(self, rabbit_url, rabbit_queue_name, rabbit_queue_max_items):
        self.rabbit_queue_name = rabbit_queue_name
        self.rabbit_queue_max_items = rabbit_queue_max_items
        self.magic_number = (self.rabbit_queue_max_items * 10) / 100

        self.url_parameters = pika.connection.URLParameters(rabbit_url)

        self.connection = pika.BlockingConnection(self.url_parameters)

        self.channel = self.connection.channel()
        self.q = self.channel.queue_declare(self.rabbit_queue_name, arguments={"x-max-priority": 10})
        self.q_name = self.q.method.queue

        # Turn on delivery confirmations
        self.channel.confirm_delivery()

        self.__init_redis()

    def __init_redis(self):
        REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
        REDIS_PORT = os.environ.get("REDIS_PORT", 6379)
        REDIS_DB = os.environ.get("REDIS_DB", 0)

        self.redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)

    def publish_block(self, block_number):
        self.channel.basic_publish("", self.q_name, str(block_number))
        logger.info("Message has been delivered: {}".format(block_number))

    def get_last_block_enqueued(self):
        last_block_enqueued = self.redis.get("last_block_enqueued")
        if last_block_enqueued:
            last_block_enqueued = int(last_block_enqueued)
        logger.info("Getting last_block_enqueued from redis: {}".format(last_block_enqueued))
        return last_block_enqueued

    def set_last_block_enqueued(self, block_number):
        logger.info("Setting last_block_enqueued to redis: {}".format(block_number))
        self.redis.set("last_block_enqueued", block_number)

    def run(self):
        last_block_enqueued = self.get_last_block_enqueued() or SYNC_FROM_BLOCK
        try:
            while True:
                last_block_processed = get_last_block_processed() or SYNC_FROM_BLOCK
                if (last_block_enqueued - last_block_processed) < MIN_QUEUE_SIZE:
                    last_block_mined = get_last_block_number()

                    logger.info("last_block.number: {}".format(last_block_mined))
                    logger.info("last_block_enqueued: {}".format(last_block_enqueued))

                    if last_block_mined != last_block_enqueued:
                        upper_block_number = min(
                            last_block_enqueued + 1 + self.rabbit_queue_max_items,
                            last_block_mined
                        )
                        blocks_to_enqueue = list(
                            range(last_block_enqueued + 1, upper_block_number + 1)
                        )

                        for block in blocks_to_enqueue:
                            self.publish_block(str(block))

                        last_block_enqueued = blocks_to_enqueue[-1]
                        self.set_last_block_enqueued(last_block_enqueued)
                    else:
                        logger.info("Sleeping for {} sec".format(SLEEP_NEW_BLOCKS))
                        self.connection.process_data_events()
                        time.sleep(SLEEP_NEW_BLOCKS)
                else:
                    logger.info("Queue Full. Sleeping for {} sec".format(SLEEP_SEC_QUEUE_FULL))
                    self.connection.process_data_events()
                    time.sleep(SLEEP_SEC_QUEUE_FULL)
        except Exception as e:
            logger.error(e, exc_info=True)
            self.connection.close()
