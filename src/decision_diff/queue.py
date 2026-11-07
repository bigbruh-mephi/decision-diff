"""RabbitMQ topology and publishing. The broker holds only reconstructible work; PostgreSQL is the source of truth."""

import aio_pika
import orjson

from .settings import settings

WORK, INTERACTIVE, DLX, DEAD = "dd.work", "dd.interactive", "dd.dlx", "dd.dead"
PRIORITY = {"ingest": 2, "chunk": 2, "finalize": 6, "cell": 6, "check": 6}


def _queue_args(limit):
    return {"x-queue-type": "quorum", "x-delivery-limit": limit, "x-dead-letter-exchange": DLX,
            "x-dead-letter-strategy": "at-least-once", "x-overflow": "reject-publish",
            "x-delayed-retry-type": "failed", "x-delayed-retry-min": 2000, "x-delayed-retry-max": 60000}


async def connect():
    """Robust connection (reconnects) with publisher confirms and the topology declared idempotently."""
    conn = await aio_pika.connect_robust(settings.amqp_url)
    ch = await conn.channel(publisher_confirms=True)
    dlx = await ch.declare_exchange(DLX, aio_pika.ExchangeType.FANOUT, durable=True)
    dead = await ch.declare_queue(DEAD, durable=True, arguments={"x-queue-type": "quorum"})
    await dead.bind(dlx)
    await ch.declare_queue(WORK, durable=True, arguments=_queue_args(settings.delivery_limit))
    await ch.declare_queue(INTERACTIVE, durable=True, arguments=_queue_args(1))
    return conn, ch


async def publish(ch, queue, task):
    """Publish a task dict persistently; ``message_id`` makes duplicates recognisable."""
    body = orjson.dumps({"v": 1, **task})
    mid = f"{task.get('run_id', task.get('exec_id'))}:{task['kind']}:{task.get('chunk_no', '')}"
    await ch.default_exchange.publish(
        aio_pika.Message(body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT, content_type="application/json",
                         message_id=mid, priority=PRIORITY.get(task["kind"], 2)), routing_key=queue)
