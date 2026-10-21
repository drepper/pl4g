// The checker's methods ported from check.py lines 15128 to 18052, defined in
// check-places.cc.  Not a header of its own: included inside the class body of `Checker` (check.hh).

    // Where a reference taken of something points, what is there, whether it may
    // be written through, what to call it, and whether it lasts as long as the
    // program: what `_place_of_a_name` and its siblings answer.
    struct PlaceTaken {
      ir::Value* address = nullptr;
      const ir::Type* held = nullptr;
      bool may_change = false;
      std::string what;
      bool lasting = false;
    };

    // What `_enter_lifetimes` sets aside, to be put back.
    struct LifetimesSaved {
      std::vector<std::tuple<std::string, NameSet>> scopes;
      Dict<std::tuple<std::string, std::string>,
           std::vector<std::tuple<ir::Value*, std::optional<std::int64_t>, std::string, bool>>>
        sources;
    };

    // How long what a value refers to is there, as a call can say it
    // (`_lifetime_of_value`): `local` with the variable, `lasting`, `names` with
    // the lifetime names of the signatures around, or `unknown`.
    struct Lifetime {
      std::string kind;
      Local* local = nullptr;
      std::set<std::pair<std::string, std::string>> names;
    };

    // Room made in a list for more (`_room_made`): where its elements were and
    // are now, how many there are, and the allocator it had and has.
    struct Room {
      ir::Value* old = nullptr;
      ir::Value* grown = nullptr;
      ir::Value* count = nullptr;
      ir::Value* had = nullptr;
      ir::Value* into = nullptr;
    };

    // Whether a place reached from `value` is there as long as the program.
    bool lasting(ir::Value* value);  // check.py:15128
    // Check that what each `return` hands back really comes from there.
    void answers_from(ir::Function* func, std::string_view name, std::span<ir::Value* const> sources, const Dict<std::int64_t, KeySet>& only, const KeySet& stored);  // check.py:15151
    // Check every answer of `func` against what its signature said each reference in it lives as long as
    // (4561).
    void answered_as_said(ir::Function* func, const Dict<std::int64_t, ir::Borrowed>& table, std::span<const std::string> names, std::span<ir::Value* const> arriving);  // check.py:15179
    // What a promised answer may come from: what the parameters carrying its name arrived as, and where a
    // parameter given storage of its own keeps it -- with the parts of a tuple the name is on -- and what
    // a name of a signature around stands for here. With what to call them, and which of them are where a
    // parameter is kept rather than what it named.
    std::tuple<std::string, std::vector<ir::Value*>, Dict<std::int64_t, KeySet>, KeySet> sources_for(const ir::Borrowed& borrowed, std::span<const std::string> names, std::span<ir::Value* const> arriving);  // check.py:15191
    // What each lifetime name in reach stands for here, for a call through a lambda whose answer lives as
    // long as one of them.
    Dict<std::pair<std::string, std::string>, std::vector<ir::Value*>> lifetime_outer();  // check.py:15234
    // Make the lifetime names of a signature, `key`, ones a lambda or a function type in the body may say
    // an answer lives as long as, and write down what each stands for: what the parameters carrying it
    // arrived as.
    LifetimesSaved enter_lifetimes(std::string_view key, std::span<const ast::Param* const> params, std::span<const ir::Type* const> types, std::span<ir::Value* const> arriving, std::span<const std::tuple<Local*, Local*, bool>> brought = {});  // check.py:15240
    // Check, for an answer that is a tuple, that each reference in it comes from the parameters its
    // lifetime name is on -- every `return`, every way through it, member by member (4561).
    void members_answered_from(ir::Function* func, const Dict<std::int64_t, std::tuple<std::string, std::vector<ir::Value*>, Dict<std::int64_t, KeySet>, KeySet>>& sources);  // check.py:15285
    // Whether the part at `leaf` of the tuple `value` was worked out from one of `sources`, or lasts as
    // long as the program.
    bool leaf_reached(ir::Value* value, std::int64_t leaf, std::span<ir::Value* const> sources, const Incoming& incoming, KeySet& visited, const Dict<std::int64_t, KeySet>& only, const Dict<std::pair<std::string, std::string>, std::vector<ir::Value*>>& outer, const KeySet& stored);  // check.py:15306
    // What a call of `func` answers with lives as long as what it borrowed from (`_lasting_as_given`).
    ir::Value* as_long_as_given(ir::Builder& builder, ir::Function* func, std::span<ir::Value* const> args, ir::Value* answer, Span span);  // check.py:15344
    // What a call answers with lives as long as what it borrowed from.
    ir::Value* lasting_as_given(ir::Builder& builder, std::string_view name, std::span<const std::string> param_names, const Dict<std::int64_t, ir::Borrowed>& table, std::span<ir::Value* const> args, ir::Value* answer, Span span);  // check.py:15352
    // `_lasting_as_given` for each reference in an answer that is a tuple.
    ir::Value* members_as_long_as_given(ir::Builder& builder, std::string_view name, std::span<const std::string> param_names, const Dict<std::int64_t, ir::Borrowed>& table, std::span<ir::Value* const> args, ir::Value* answer, Span span);  // check.py:15390
    // How long the answer of one call lives, in a sentence.
    std::string how_long(std::span<const std::string> param_names, const ir::Borrowed& borrowed, std::span<const std::tuple<ir::Value*, std::optional<std::int64_t>>> parts, bool lasting);  // check.py:15438
    // The shortest-lived name whose place `value` reaches, where it reaches one -- of its part `leaf`,
    // where it is a tuple and one part is meant.
    Local* named_place_of(ir::Value* value, std::optional<std::int64_t> leaf = {});  // check.py:15487
    // What parameter `at` of the function being lowered arrived as, and where it is kept if it was given
    // storage of its own.
    std::vector<ir::Value*> parameter_sources(std::int64_t at);  // check.py:15530
    // Whether the signature says `value` lives as long as the reference the place it is written into
    // holds: the place reached from a parameter whose held reference carries a lifetime name, the value
    // from one whose own reference carries the same. Each call is held to it (`_kept_alike`).
    bool said_alike(ir::Value* place, ir::Value* value);  // check.py:15543
    // What a lambda written as an argument answers, where it says nothing of how long a reference it
    // answers lives and the parameter it is handed for does, in a name of the signature being called:
    // that, which stands at the call for what the call hands over -- the call being what is held to it.
    std::optional<Dict<std::int64_t, ir::Borrowed>> adopted_borrows(const ast::Lambda* expr, const ir::Type* answer, const ir::Type* expected);  // check.py:15555
    // How long what `value` refers to is there, as a call can say it: a variable of this function, with
    // its depth; one or more names of the signature around, which outlast its body; as long as the
    // program; or nothing that can be said.
    Lifetime lifetime_of_value(ir::Value* value);  // check.py:15572
    // Whether what `answer` says lives at least as long as `given`.
    bool lasts_as_long(const Lifetime& answer, const Lifetime& given);  // check.py:15587
    // Hold a call to what a function type among its parameters says: that what a function handed for it
    // answers lives as long as what is handed for the parameters carrying the same name (4930).
    void answers_as_long(ir::Function* func, std::span<ir::Value* const> args, const Dict<std::int64_t, std::vector<std::tuple<bool, Local*>>>& notes, Span span);  // check.py:15601
    // Hold a call to what its signature says lives as long as what.
    void kept_alike(ir::Function* func, std::span<ir::Value* const> args, Span span);  // check.py:15649
    // Report a reference given to a name that outlives what it names.
    bool outlives_it(ir::Value* value, std::int64_t depth, std::string_view where, Span span);  // check.py:15670
    // Where the name `expr` is, for a reference being taken of it.
    std::optional<PlaceTaken> place_of_a_name(ir::Builder& builder, const ast::NameRef* expr);  // check.py:15689
    // The variable another module exports under this path, or nothing.
    ir::GlobalVar* exported_variable(const ast::Member* expr);  // check.py:15722
    // Where one field of a record is, for a reference being taken of it.
    std::optional<PlaceTaken> place_of_a_field(ir::Builder& builder, const ast::Member* expr, bool writing = false);  // check.py:15740
    // Where one element of an array is, for a reference being taken of it.
    // Where one element of an array is, for a reference being taken of it --
    // or of a list, where `of_a_list` says the place is only read, there and
    // then, a field at a time.
    std::optional<PlaceTaken> place_of_an_element(ir::Builder& builder, const ast::Element* expr,
                                                  bool of_a_list = false);  // check.py:15934
    // Whether the field `name` of the record `base` is writable whatever the record is: it, or a field on
    // the way to it, is written `mut NAME`, and no `&` that only reads lies on the way -- what a `&` lends
    // is read-only however it is made.
    bool lifted(const ast::Expr* base, std::string_view name, const ir::ProductType* ty);  // check.py:15800
    // Whether `expr` is reached through a `&` that may not write.
    bool behind_a_reader(const ast::Expr* expr);  // check.py:15813
    // The type of a place written as a path -- a name, a field of one, what a reference names -- read off
    // the definitions, with nothing lowered.
    const ir::Type* record_type_of(const ast::Expr* expr);  // check.py:15827
    // Whether what `expr` names may be written, asked quietly of its root: a name that is `mut`, a
    // reference that is `&mut`.
    bool may_write(const ast::Expr* expr);  // check.py:15851
    // Whether `value` is, or is worked out from, a variable of the image nothing may write: one that is
    // not `mut`, or what was put `in ⎕static`.
    bool read_only(ir::Value* value);  // check.py:15878
    // Check a set or a dictionary written down.
    ir::Value* lower_collection(ir::Builder& builder, const ast::Expr* expr, const ir::Type* expected);  // check.py:15895
    // Where the allocator something was told to come out of keeps its state.
    ir::Value* arena_named(ir::Builder& builder, const ast::Expr* written);  // check.py:15962
    // Make the table a collection is, and put what was written down in it.
    ir::Value* build_collection(ir::Builder& builder, const ast::Expr* expr, const ir::Type* ty, ir::Value* arena, Entries ready);  // check.py:16009
    // What the entries of a table of this type are shaped by.
    tables::Shape table_shape(const ir::Type* ty);  // check.py:16034
    // Make an empty table of the shape `ty` calls for.
    ir::Value* new_table(ir::Builder& builder, const ir::Type* ty, Span span, ir::Value* arena = nullptr, ir::Value* comes_from = nullptr);  // check.py:16046
    // A key as the table holds it.
    ir::Value* as_held(ir::Builder& builder, const tables::Shape& shape, ir::Value* key, Span span);  // check.py:16072
    // The entry for `key`, made if the table did not have it.
    ir::Value* put_key(ir::Builder& builder, const tables::Shape& shape, ir::Value* table, ir::Value* key, Span span);  // check.py:16089
    // The entry the key is in, or the one it would go in.
    ir::Value* find_key(ir::Builder& builder, const tables::Shape& shape, ir::Value* table, ir::Value* key, Span span);  // check.py:16100
    // Whether an entry holds a key rather than standing empty.
    ir::Value* is_live(ir::Builder& builder, ir::Value* place, Span span);  // check.py:16111
    // Where in an entry the value belonging to its key is.
    ir::Value* value_place(ir::Builder& builder, const tables::Shape& shape, ir::Value* place, const ir::Type* ty);  // check.py:16116
    // Lower each of `written` and give back the one type they share.
    const ir::Type* one_type(ir::Builder& builder, std::span<const ast::Expr* const> written, const ir::Type* wanted, std::vector<ir::Value*>* into = nullptr);  // check.py:16124
    // The type the entries of a literal say they hold, where one says.
    const ir::Type* said_by(std::span<const ast::Expr* const> written);  // check.py:16147
    // Hand `written` back with the surrounding context put aside.
    std::vector<const ast::Expr*> nothing_expected(std::span<const ast::Expr* const> written);  // check.py:16169
    // The one type several values share, reporting one that does not.
    const ir::Type* same_type(std::span<ir::Value* const> values, std::span<const Span> spans, const ir::Type* wanted);  // check.py:16184
    // Lower a collection's entries, once each and in the order written.
    Entries entries_written(ir::Builder& builder, std::span<const std::tuple<const ast::Expr*, const ast::Expr*>> written, const ir::Type* expected);  // check.py:16207
    // Check `c⸨k⸩`: whether a set holds a key, or what a dictionary has.
    ir::Value* lower_index(ir::Builder& builder, const ast::Index* expr, const ir::Type* expected);  // check.py:16249
    // Check `†c⸨k⸩`, which takes a key out and answers what was there.
    ir::Value* lower_take(ir::Builder& builder, const ast::Take* expr, const ir::Type* expected);  // check.py:16297

    // -- lists, and the cursors that walk them -----------------------------------
    // The place a list is in, for something that is about to change it -- `†` where `changing` is nothing,
    // and what it names otherwise.
    std::optional<std::tuple<ir::Value*, const ir::ListType*>> list_place(ir::Builder& builder, const ast::Expr* expr, Span span, std::optional<std::string> changing = {});  // check.py:16357
    // The list in a place, taken apart: where, how many, and the list.
    std::tuple<ir::Value*, ir::Value*, ir::Value*> list_at(ir::Builder& builder, ir::Value* place, const ir::ListType* ty, Span span);  // check.py:16405
    // Move what follows one element down, and write the shorter list back.
    void erased(ir::Builder& builder, ir::Value* place, const ir::ListType* ty, ir::Value* elements, ir::Value* count, ir::Value* at, Span span);  // check.py:16412
    // The element `at` places along, read where the elements are.
    ir::Value* element_of(ir::Builder& builder, ir::Value* elements, const ir::ListType* ty, ir::Value* at, Span span);  // check.py:16429
    // Check `†l⟦i⟧`, which takes one element out and answers what it was.
    ir::Value* lower_take_at(ir::Builder& builder, const ast::TakeAt* expr, const ir::Type* expected);  // check.py:16438
    // Room in the list at `place` for `more` elements after the ones it has.
    Room room_made(ir::Builder& builder, ir::Value* place, const ir::ListType* ty, ir::Value* more, Span span);  // check.py:16468
    // The list at `place` written back with `count` elements in the room `_room_made` made, and the room
    // it grew out of given back.
    void room_kept(ir::Builder& builder, ir::Value* place, const ir::ListType* ty, const Room& made, ir::Value* count, Span span);  // check.py:16491
    // The list at `place` in room of its own before it is written in place: a list in the image is copied
    // out, into the arena in force, and any other is as it was -- room for nought more being room it has.
    void made_writable(ir::Builder& builder, ir::Value* place, const ir::ListType* ty, Span span, const ast::Expr* written = nullptr);  // check.py:16505
    // Note `written` as a value to copy where it is lowered, where it is a list read out of a place into
    // another -- one that may itself change it in place where `changes` says so -- and the two would
    // otherwise share the room the elements are in.
    void copied_where_shared(const ast::Expr* written, bool changes);  // check.py:16524
    // What a call hands the parameters `func` takes as its own: a copy, or -- where the argument is room
    // the caller is done with -- that room.
    std::vector<ir::Value*> owned_arguments(ir::Builder& builder, const ast::Call* expr, ir::Function* func, std::span<ir::Value* const> args);  // check.py:16556
    // Whether `written`, handed to a parameter that is taken as the callee's own, may be handed over as it
    // is rather than copied: `std::move`.
    bool movable(const ast::Expr* written);  // check.py:16578
    // Whether `written` is a parameter `func` takes as its own.
    bool owned_parameter(const ast::Expr* written, ir::Function* func);  // check.py:16600
    // `value` as it is where its allocator is `⎕heap`, and a copy in the heap where it is anything else --
    // an arena of the caller's -- asked while running, since which it is was the caller's to know.
    ir::Value* heaps_or_copied(ir::Builder& builder, ir::Value* value, const ir::ListType* ty, Span span);  // check.py:16609
    // A list of elements of its own, deep, in `⎕heap`, as a body copying what it takes as its own on the
    // way in made it.
    ir::Value* copy_into_heap(ir::Builder& builder, ir::Value* value, Span span);  // check.py:16636
    // A `mut` parameter holding a list, given elements of its own where the body changes it in place: what
    // arrived is in its caller's room, which a change through the parameter would otherwise change for the
    // caller.
    ir::Value* own_when_changed(ir::Builder& builder, const ast::Param* param, ir::Value* value, const ast::Node* body);  // check.py:16647
    // Whether `local` is used again after the statement being lowered -- on any way on from it, the loop
    // it is in included.
    bool used_after(Local* local);  // check.py:16657
    // A list of elements of its own, deep, in the arena in force -- or a record or a tuple holding one,
    // with every list it holds so copied.
    ir::Value* copy_of(ir::Builder& builder, ir::Value* value, Span span);  // check.py:16664
    // Lower `l ⧺← more`: `more` joined onto the end of the list in the place `l`, in the room it has to
    // spare and in new room twice as large where it has not enough.
    void lower_join_assign(ir::Builder& builder, const ast::JoinAssign* stmt);  // check.py:16673
    /// Whether what is joined onto a list is, by the look of it, an array and not a list: a name or a
    /// call that says so, a slice, the bytes of a string.  Anything else is lowered as the list it has
    /// to be, which is what lets `[]` and the arms of an `if` take the list's type.
    bool may_be_an_array(const ast::Expr* written);
    /// Whether a view of `element` may be copied into a list, saying why not where it may not: as an
    /// array is copied (4710), by its bytes.
    bool view_copied(const ir::Type* element, const ast::Expr* written);
    /// Copy the elements of `view`, a view of one dimension, into the room at `into`, `after`
    /// elements along: one move of their bytes where they are one run, an element at a time where
    /// they are a step apart.
    void joined_on(ir::Builder& builder, ir::Value* into, ir::Value* after, ir::Value* view, const ir::Type* element,
                   Span span);
    // `l⟦i⟧`: the element at `i`, which is checked against how many there are while running, a list's
    // length not being in its type.
    ir::Value* list_element(ir::Builder& builder, const ast::Element* expr, ir::Value* base, const ir::ListType* ty, const ir::Type* expected);  // check.py:16747
    // `l⟦i⟧ ← v`: one element of the list in the place `l` replaced, where it is -- the list copied out of
    // the image first, which is read-only, and an element that points somewhere copied into the list's
    // allocator, the one it replaces given back.
    void lower_list_element_assign(ir::Builder& builder, const ast::ElementAssign* stmt);  // check.py:16768
    // `⎕reserve(l, n)`: room in the list in the place `l` for `n` more, made now, so that joining that
    // many on takes no more room.
    ir::Value* lower_reserve(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:16811
    // An index written for a list, read as the count it is.
    ir::Value* index_into(ir::Builder& builder, const ast::Expr* written, Span span);  // check.py:16837
    // A cursor taken apart: the place the list is in, and how far along.
    std::tuple<ir::Value*, ir::Value*, const ir::ListType*> cursor_parts(ir::Builder& builder, ir::Value* value, const ir::CursorType* ty, Span span);  // check.py:16860
    // Lower `⎕iter(l)`: where a walk over the list in `l` begins.
    ir::Value* lower_iter(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:16869
    // Lower `⇧it` and `⇩it`: the cursor at the next element, and at the one before.
    ir::Value* lower_step(ir::Builder& builder, const ast::Unary* expr, ir::Value* value, const ir::CursorType* ty, const ir::Type* expected);  // check.py:16908
    // Check `†it`: the element the cursor is at goes, and the cursor is answered with.
    ir::Value* lower_take_through(ir::Builder& builder, const ast::TakeThrough* expr, const ir::Type* expected);  // check.py:16939
    // `it⌖`: the element a cursor is at.
    ir::Value* through_a_cursor(ir::Builder& builder, ir::Value* value, const ir::CursorType* ty, Span span);  // check.py:16969
    // What a condition comes to, where what was written is a cursor.
    ir::Value* asked(ir::Builder& builder, ir::Value* value, Span span);  // check.py:16983
    // Check `d⸨k⸩ ← v`, which puts a value under a key.
    void lower_entry_assign(ir::Builder& builder, const ast::EntryAssign* stmt);  // check.py:16998

    // -- if ----------------------------------------------------------------------
    // What a condition written after `comptime` comes to, or nothing where it is not a question the
    // compiler can settle.
    std::optional<bool> settled(const ast::Expr* condition);  // check.py:17024
    // Whether `⎕isrecord` was asked of a record.
    // Whether `⎕kindof(⌜T⌝) = "KIND"` holds, where `condition` asks it.
    std::optional<bool> settled_kind(const ast::Binary* condition);
    std::optional<bool> settled_is_record(const ast::Call* call);  // check.py:17060
    // Both sides of a logical operator, where both are settled.
    std::optional<bool> both_settled(const ast::Binary* condition, const std::function<bool(std::span<const bool>)>& joined);  // check.py:17079
    // The type an operand of a compile-time comparison stands for.
    const ir::Type* type_stood_for(const ast::Expr* expr);  // check.py:17087
    // The type `⌜…⌝` lifted, or nothing where it lifted an expression.
    const ir::Type* lifted_type(const ast::Lifted* expr);  // check.py:17110
    // The type of what a lift lifted, which is what `⎕typeof` answers.
    const ir::Type* type_of_a_lift(const ast::Expr* asked);  // check.py:17128
    // The arms left once every one the compiler settled has been answered.
    std::optional<std::vector<const ast::IfArm*>> chosen_arms(const ast::If* stmt);  // check.py:17163
    // Lower statements a macro wrote where a value stands.
    ir::Value* lower_scoped(ir::Builder& builder, const ast::Scoped* expr, const ir::Type* wanted);  // check.py:17187
    // Check and lower an `if`, its `elif`s and its `else`.
    ir::Value* lower_if(ir::Builder& builder, const ast::If* stmt, ir::Function* func, const ir::Type* wanted, bool produces);  // check.py:17203

    // -- loops -------------------------------------------------------------------
    // Check and lower a `while`, which is a branch backwards.
    ir::Value* lower_while(ir::Builder& builder, const ast::While* stmt, ir::Function* func, const ir::Type* expected, bool produces);  // check.py:17309
    // Check and lower a `foreach`, and `while` written with a binding.
    ir::Value* lower_foreach(ir::Builder& builder, const ast::ForEach* stmt, ir::Function* func, const ir::Type* expected, bool produces);  // check.py:17414
    // The loop itself, over what `found` says to walk.
    ir::Value* lower_walk(ir::Builder& builder, const ast::ForEach* stmt, ir::Function* func, const ir::Type* expected, bool produces, Iteration found);  // check.py:17516

    // -- naming a loop, and leaving or repeating it ------------------------------
    // The name a loop may take, which is the one written unless it is taken.
    const ast::Label* label_of(const ast::Label* label);  // check.py:17600
    // Put a loop's name up for the length of its body.
    Loop* begin_loop(const ast::Label* label, ir::BasicBlock* header, ir::BasicBlock* after, std::span<Local* const> carried, std::span<ir::Value* const> state, const Step& step, bool answers, bool wraps, const ir::Type* handing);  // check.py:17620
    // Take it down again, and report a name nothing named.
    Loop* end_loop(const ast::Label* label);  // check.py:17637
    // Give the block after a loop the names every way out of it hands over.
    std::tuple<std::vector<ir::Value*>, ir::Value*> exit_params(ir::BasicBlock* after, std::span<Local* const> carried, std::span<ir::Value* const> params);  // check.py:17646
    // The loop a jump names, or nothing where it names none.
    Loop* find_loop(const ast::Label* label);  // check.py:17667
    // Lower `break §name [VALUE]`: go to where the loop of that name ends.
    void lower_break(ir::Builder& builder, const ast::Break* stmt);  // check.py:17676
    // What a `break` hands the loop, or nothing where it hands it nothing.
    ir::Value* handed_over(ir::Builder& builder, const ast::Break* stmt, Loop* found);  // check.py:17705
    // Lower `continue §name`: begin the next turn of the loop of that name.
    void lower_continue(ir::Builder& builder, const ast::Continue* stmt);  // check.py:17751
    // Say that what is lowered next is what the loop comes to.
    LoopContext as_the_loops_value();  // check.py:17779
    // Put back what `_as_the_loops_value` set aside.
    void as_it_was(const LoopContext& outer);  // check.py:17793
    // What a `break` hands over, where the loop's own type already says.
    const ir::Type* wanted_of_a_loop(const ast::Expr* stmt, const ir::Type* expected, bool produces);  // check.py:17798
    // Fill the way out of the test, and answer what the loop comes to.
    ir::Value* loop_answer(ir::Builder& builder, const ast::Expr* stmt, ir::Function* func, Loop* one, ir::BasicBlock* leave, ir::BasicBlock* after, std::span<Local* const> carried, std::span<ir::Value* const> params, ir::Value* token, ir::Value* exit_token, ir::Value* ran_out, const std::vector<ir::Value*>* ways, const ir::Type* expected, bool produces);  // check.py:17815
    // Branch out of the test and stand in the block the loop ends at.
    void settle_after(ir::Builder& builder, std::span<Local* const> carried, const std::vector<ir::Value*>* ways, std::span<ir::Value* const> params, ir::BasicBlock* after, ir::Value* exit_token, ir::Value* token, const ast::Expr* stmt, ir::BasicBlock* leave, ir::Value* given);  // check.py:17893
    // Bind what a turn gave to the names the loop was written with.
    void bind_turn(ir::Builder& builder, const ast::ForEach* stmt, const Iteration& found, std::span<ir::Value* const> state);  // check.py:17910
    // Report a name a turn would copy a value that is never copied into.
    bool never_copied(const ir::Type* ty, std::string_view name, Span span);  // check.py:17949
    // Bind the names of a turn, some of them as references to the element.
    void bind_by_reference(ir::Builder& builder, const ast::ForEach* stmt, const Iteration& found, std::span<ir::Value* const> state, std::span<const std::tuple<std::string, Span>> names);  // check.py:17959
    // What a loop walks the elements of: what `⎕enumerate` was handed, where it counts the turns, and the
    // expression itself otherwise.
    static const ast::Expr* walked_expr(const ast::Expr* expr);  // check.py:18027
    // The local name what a loop walks is, or under, where it is one.
    std::optional<std::string> walked_name(const ast::Expr* expr);  // check.py:18035
    // Whether what a loop walks is a place that may be written.
    bool walked_may_change(const ast::Expr* expr);  // check.py:18043
