/** A command was dropped because a newer one, or a Stop, replaced it before it ran. */
export class Superseded extends Error {
  constructor() {
    super("superseded");
    this.name = "Superseded";
  }
}

interface Waiter {
  run: () => Promise<unknown>;
  resolve: (value: unknown) => void;
  reject: (error: unknown) => void;
}

/**
 * One device's commands, sent one at a time.
 *
 * Commands share a key when a later one makes an earlier one pointless (two temperature
 * changes for the same zone): only the latest of a key still waiting is sent, and the
 * earlier callers of that key are rejected with `Superseded`. `stop` drops everything waiting and runs at
 * once, without waiting for a command in flight, because Stop must never queue behind a
 * change it is meant to cancel. The integration orders writes on its side too; this keeps
 * the panel from flooding it.
 */
export class CommandQueue {
  private readonly order: string[] = [];
  private readonly waiting = new Map<string, Waiter[]>();
  private busy = false;

  enqueue<T>(key: string, run: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const waiter: Waiter = {
        run,
        resolve: resolve as (value: unknown) => void,
        reject,
      };
      const queued = this.waiting.get(key);
      if (queued) {
        queued.push(waiter);
      } else {
        this.waiting.set(key, [waiter]);
        this.order.push(key);
      }
      void this.pump();
    });
  }

  stop<T>(run: () => Promise<T>): Promise<T> {
    for (const waiters of this.waiting.values()) {
      for (const waiter of waiters) waiter.reject(new Superseded());
    }
    this.waiting.clear();
    this.order.length = 0;
    return run();
  }

  get pending(): number {
    return this.order.length;
  }

  private async pump(): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    try {
      while (this.order.length) {
        const key = this.order.shift()!;
        const waiters = this.waiting.get(key);
        this.waiting.delete(key);
        if (!waiters?.length) continue;
        const latest = waiters[waiters.length - 1];
        for (const dropped of waiters.slice(0, -1)) dropped.reject(new Superseded());
        try {
          latest.resolve(await latest.run());
        } catch (error) {
          latest.reject(error);
        }
      }
    } finally {
      this.busy = false;
    }
  }
}
