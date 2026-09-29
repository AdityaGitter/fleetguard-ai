# ADR-002: Kafka topic keyed by VIN

**Status**: Accepted

**Context**: Out-of-order and duplicate telemetry events are expected
(network jitter, retries, multi-channel sensor bursts). Downstream feature
computation (rolling windows per vehicle) depends on being able to reason
about ordering per vehicle, even if global ordering across vehicles isn't
guaranteed.

**Decision**: Produce every telemetry event with `key=vin`. Kafka guarantees
ordering within a partition, and keying by VIN means all of one vehicle's
events land on the same partition — so per-vehicle ordering is preserved
even though global ordering across 100k vehicles is not (and doesn't need
to be).

**Consequences**:
- (+) Rolling-window feature computation per VIN can rely on in-partition
  order, simplifying the stream-processing logic.
- (+) Consumer group scaling is straightforward: add partitions and consumer
  instances; each instance owns a disjoint set of VINs.
- (−) Skewed load is possible if a small number of VINs dominate traffic
  (not expected here — each vehicle emits at a similar rate) — documented
  as a watch-item, not a current problem.
