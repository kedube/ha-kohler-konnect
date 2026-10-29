import { describe, expect, it } from "vitest";
import { CommandQueue, Superseded } from "../src/util/queue";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}

describe("CommandQueue", () => {
  it("sends one command at a time, in order", async () => {
    const queue = new CommandQueue();
    const log: string[] = [];
    const gate = deferred();
    const first = queue.enqueue("a", async () => {
      log.push("a start");
      await gate.promise;
      log.push("a end");
    });
    const second = queue.enqueue("b", async () => void log.push("b"));
    await Promise.resolve();
    expect(log).toEqual(["a start"]);
    gate.resolve();
    await Promise.all([first, second]);
    expect(log).toEqual(["a start", "a end", "b"]);
  });

  it("sends only the latest of a key still waiting", async () => {
    const queue = new CommandQueue();
    const sent: number[] = [];
    const gate = deferred();
    const busy = queue.enqueue("busy", () => gate.promise);
    const early = queue.enqueue("temperature", async () => void sent.push(100));
    const late = queue.enqueue("temperature", async () => void sent.push(104));
    gate.resolve();
    await busy;
    await expect(early).rejects.toBeInstanceOf(Superseded);
    await late;
    expect(sent).toEqual([104]);
  });

  it("runs Stop at once and drops everything waiting", async () => {
    const queue = new CommandQueue();
    const sent: string[] = [];
    const gate = deferred();
    const busy = queue.enqueue("busy", () => gate.promise);
    const waiting = queue.enqueue("outlet", async () => void sent.push("outlet"));
    await queue.stop(async () => void sent.push("stop"));
    expect(sent).toEqual(["stop"]);
    await expect(waiting).rejects.toBeInstanceOf(Superseded);
    gate.resolve();
    await busy;
    expect(queue.pending).toBe(0);
    expect(sent).toEqual(["stop"]);
  });

  it("reports a failed command to its caller and carries on", async () => {
    const queue = new CommandQueue();
    const failed = queue.enqueue("a", async () => {
      throw new Error("The valve is offline");
    });
    const next = queue.enqueue("b", async () => "ok");
    await expect(failed).rejects.toThrow("The valve is offline");
    await expect(next).resolves.toBe("ok");
  });
});
