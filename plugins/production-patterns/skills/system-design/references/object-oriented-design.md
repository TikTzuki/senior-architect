# Object-Oriented Design Exercises

**Pin down the assumptions, model the nouns as classes and the rules as methods on the class that owns the data, and
spend your design effort on the one decision that makes the core operation cheap or correct.**

The assumptions below are the ones the primer's reference code encodes (it is a skeleton: many methods are stubs). Where
the source code has bugs, the note says so rather than copying them.

## Hash map

- **Assumptions:** integer keys; fixed table size (no resizing); collisions resolved by chaining; `get`/`remove` of a
  missing key raises `KeyError`.
- **Classes:**
    - `Item(key, value)`: one stored pair.
    - `HashTable(size)`: `table` is a list of `size` buckets (lists); `_hash_function(key) = key % size`; `set`
      overwrites in place if the key is already in the bucket, else appends; `get` and `remove` scan the bucket.
- **The decision that matters:** collision strategy. Chaining keeps `set`/`get` O(1) average and O(n) worst case when
  one bucket fills; with no resize, the load factor and so the chain length grow without bound.

## LRU cache

- **Assumptions:** fixed capacity `MAX_SIZE`; key is a query, value its results; `get` of a miss returns `None`; both
  reads and writes refresh recency; evict the least recently used entry on insert at capacity.
- **Classes:**
    - `Node`: holds the value (and must hold its key, see below), plus prev/next links.
    - `LinkedList`: `head` (most recent), `tail` (least recent); `move_to_front`, `append_to_front`, `remove_from_tail`.
    - `Cache`: `lookup` dict key → node, `linked_list`, `size`.
- **Shape:**
  ```python
  class Cache:
      def get(self, key):
          node = self.lookup.get(key)
          if node is None: return None
          self.list.move_to_front(node); return node.value
      def set(self, key, value):
          node = self.lookup.get(key)
          if node: node.value = value; self.list.move_to_front(node); return
          if self.size == self.MAX_SIZE:
              self.lookup.pop(self.list.tail.key); self.list.remove_from_tail()
          else: self.size += 1
          node = Node(key, value); self.list.append_to_front(node); self.lookup[key] = node
  ```
- **The decision that matters:** hash map for O(1) find plus doubly linked list for O(1) reorder and evict. The node
  must carry its key so eviction from the tail can delete the map entry; the source's `Node` stores only results yet
  evicts via `tail.query`.

## Call center

- **Assumptions:** three ranks, operator < supervisor < director; a call carries the rank it needs; dispatch tries the
  lowest suitable free employee and falls through upward; if nobody is free the call waits in a FIFO queue; an employee
  always succeeds in taking a call; directors can handle anything.
- **Classes:**
    - `Rank` enum; `CallState` enum: READY, IN_PROGRESS, COMPLETE.
    - `Call`: state, required rank, assigned employee.
    - `Employee` (abstract): id, name, rank, current call, back-reference to the center; `take_call`, `complete_call` (
      notifies center), abstract `escalate_call`.
    - `Operator`, `Supervisor`: escalate by raising the call's rank one level, clearing their own call, notifying the
      center. `Director`: escalation not allowed.
    - `CallCenter`: employee lists per rank, `queued_calls` deque; `dispatch_call`, `notify_call_escalated`,
      `notify_call_completed`, dispatch of a queued call to a newly freed employee (stubs).
- **The decision that matters:** escalation is polymorphic (each subclass knows the next rank) while routing and queuing
  live only in `CallCenter`; employees report events back rather than finding each other. The source's subclasses call
  `super(Operator, ...)` and omit `call_center`, so they would not run as written.

## Deck of cards

- **Assumptions:** standard four suits, values 1–13; the generic deck is specialised for blackjack; an ace scores more
  than one way (the code stores it as 1 and leaves the alternatives to `possible_scores`), face cards (11–13) count 10;
  dealing is sequential from a shuffled deck.
- **Classes:**
    - `Suit` enum.
    - `Card` (abstract): value, suit, `is_available`; abstract `value` property.
    - `BlackJackCard`: `is_ace`, `is_face_card`; `value` maps face cards to 10; setter validates 1–13.
    - `Hand`: list of cards, `add_card`, `score` = sum. `BlackJackHand`: `possible_scores` (aces expand the set; stub)
      and `score` picks the highest total ≤ 21, else the lowest bust.
    - `Deck`: cards and `deal_index`; `deal_card` marks the card unavailable and advances, returns `None` when empty;
      `remaining_cards`; `shuffle` (stub).
- **The decision that matters:** keep game rules out of the generic types. `Card`/`Hand`/`Deck` are reusable and
  blackjack lives in subclasses; ace ambiguity is handled by scoring a set of possible totals, not by mutating the card.

## Parking lot

- **Assumptions:** multiple levels with rows of spots; spot sizes motorcycle, compact, large; a motorcycle fits any
  spot, a car fits compact or large, a bus needs 5 consecutive large spots; park on the first level that has room.
- **Classes:**
    - `VehicleSize` enum.
    - `Vehicle` (abstract): size, plate, number of spots needed, spots taken; `take_spot`, `clear_spots`, abstract
      `can_fit_in_spot`. `Motorcycle`, `Car`, `Bus` implement the fit rule.
    - `ParkingLot`: levels; `park_vehicle` tries each level in turn.
    - `Level`: floor, spots, available count; `park_vehicle`, `_find_available_spot`, `_park_starting_at_spot` (
      contiguous run for a bus), `spot_freed`.
    - `ParkingSpot`: level, row, number, size, current vehicle; `is_available`, `can_fit_vehicle` (empty and the vehicle
      accepts it).
- **The decision that matters:** the fit rule belongs to the vehicle (`can_fit_in_spot`, double dispatch from the spot),
  and a vehicle may hold several spots, so the bus case is a search for a contiguous run on a level rather than a
  special type of spot.

## Online chat

- **Assumptions:** registered users with friend requests (send, approve, reject; status UNREAD, READ, ACCEPTED,
  REJECTED); one-to-one private chats between friends and multi-user group chats; messages carry id, text and timestamp.
  No networking or persistence modelled.
- **Classes:**
    - `UserService`: registry `users_by_id`; add/remove user; brokers friend requests between two user ids.
    - `User`: id, name, password hash; dicts of friends, private chats keyed by friend id, group chats keyed by chat id,
      sent and received requests keyed by the other user; `message_user`, `message_group`, request methods.
    - `Chat` (abstract): chat id, users, messages. `PrivateChat` (exactly two users), `GroupChat` (`add_user`,
      `remove_user`).
    - `Message`, `AddRequest(from, to, status, timestamp)`, `RequestStatus` enum.
- **The decision that matters:** a friendship is a request object with a lifecycle rather than a direct mutation, and
  every relationship is a dict keyed by id so lookups from a user are O(1); private and group chats share one `Chat`
  base so messaging code does not branch on chat type.

## How to approach an OOD question

1. **State the assumptions out loud** before any class: sizes and limits (table size, cache capacity, ranks, spot
   types), what happens on the edge (miss, full, nobody free, bust), what is out of scope.
2. **Nouns → classes, fixed sets → enums.** Every exercise has enums for closed vocabularies (rank, call state, suit,
   vehicle size, request status).
3. **Put each rule on the class that owns its data.** Fit rules on vehicles, scoring on hands, escalation on employees;
   a coordinator (`CallCenter`, `ParkingLot`, `UserService`) owns allocation and lookup across many objects.
4. **Use abstract bases plus small subclasses** for variation in behaviour, and keep the generic layer reusable (deck vs
   blackjack, chat vs private/group).
5. **Choose the data structure that makes the hot operation O(1)**: dicts keyed by id everywhere, hash map + linked list
   for LRU, deque for waiting calls, index pointer for dealing.
6. **Model state transitions explicitly** (call READY → IN_PROGRESS → COMPLETE; request UNREAD → ACCEPTED/REJECTED)
   instead of inferring them from flags.
7. **Have children report up** (employee notifies the center; the level exposes `spot_freed` to update its count) rather
   than reaching sideways into peers.
8. **Ask how much code is wanted**: signatures and the core method are usually enough; stub the rest.

*Compressed from [donnemartin/system-design-primer](https://github.com/donnemartin/system-design-primer) (CC BY
4.0), `solutions/object_oriented_design/{hash_table,lru_cache,call_center,deck_of_cards,parking_lot,online_chat}/*.py`.
Structure and wording are this repository's; class designs are the primer's; the bug notes are this repository's.*
