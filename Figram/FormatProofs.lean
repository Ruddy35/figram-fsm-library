import Figram.Format

/-!
# Свойства формата

1. `decode_encode` — **полнота и точность**: любой корректный документ
   записывается в JSON и читается обратно в точности тем же, включая
   неизвестные ключи и их порядок.
2. `encode_injective` — **однозначность**: разные документы дают разный JSON.
3. `decode_requires` — **безопасность расширений**: принятый документ
   требует только известных читателю возможностей.
4. `toChart_strip` — **неизвестные ключи не влияют на поведение бота**.
5. `toChart_decls` — **описания условий, действий и переменных
   не влияют на поведение бота**: это документация для разработчика.
-/

namespace Figram.Format

/-! ## Условия корректности документа -/

def OnWF (on : List (EventId × List Variant)) : Prop :=
  ∀ p ∈ on, ∀ v ∈ p.2, extOK variantKeys v.ext

mutual
def NodeWF : NodeDoc → Prop
  | .message on _ ext => OnWF on ∧ extOK nodeKeys ext
  | .sec _ cs on ext => ChildrenWF cs ∧ OnWF on ∧ extOK nodeKeys ext
def ChildrenWF : List (NodeId × NodeDoc) → Prop
  | [] => True
  | (_, n) :: rest => NodeWF n ∧ ChildrenWF rest
end

def NodeDoc.isSec : NodeDoc → Bool
  | .sec .. => true
  | .message .. => false

structure DocWF (d : Doc) : Prop where
  requires : ∀ f ∈ d.requires, f ∈ knownFeatures
  bot : NodeWF d.bot
  botSec : d.bot.isSec = true
  ext : extOK docKeys d.ext

/-! ## Вспомогательные леммы -/

theorem find?_ext_none {known : List String} {ext : Obj} (h : extOK known ext)
    {k : String} (hk : k ∈ known) : ext.find? (fun p => decide (p.1 = k)) = none := by
  apply List.find?_eq_none.mpr
  intro p hp hpk
  have := h p hp
  simp only [decide_eq_true_eq] at hpk
  exact this (hpk ▸ hk)

theorem get_append_ext {known : List String} {ext : Obj} (h : extOK known ext)
    (A : Obj) {k : String} (hk : k ∈ known) : get (A ++ ext) k = get A k := by
  simp only [get, List.find?_append, find?_ext_none h hk, Option.or_none]

theorem unknown_append_ext {known : List String} {ext : Obj} (h : extOK known ext)
    (A : Obj) (hA : ∀ p ∈ A, p.1 ∈ known) : unknown known (A ++ ext) = ext := by
  simp only [unknown, List.filter_append]
  have h1 : A.filter (fun p => decide (p.1 ∉ known)) = [] := by
    apply List.filter_eq_nil_iff.mpr
    intro p hp
    simp [hA p hp]
  have h2 : ext.filter (fun p => decide (p.1 ∉ known)) = ext := by
    apply List.filter_eq_self.mpr
    intro p hp
    simp [h p hp]
  rw [h1, h2, List.nil_append]

theorem decStrs_map (xs : List String) : decStrs (xs.map .str) = .ok xs := by
  induction xs with
  | nil => rfl
  | cons x xs ih => simp [decStrs, ih, Except.map]

theorem decStrs_cons_map (x : String) (xs : List String) :
    decStrs (J.str x :: xs.map .str) = .ok (x :: xs) := decStrs_map (x :: xs)

theorem decStrMap_enc (m : List (String × String)) : decStrMap (encStrMap m) = .ok m := by
  induction m with
  | nil => rfl
  | cons p m ih => simp [encStrMap, decStrMap] at *; simp [ih, Except.map]

theorem numToNat_nat (n : Nat) : numToNat n 0 = some n := by
  simp [numToNat]
  intro h
  exact h.symm

theorem decNatMap_enc (m : List (String × Nat)) : decNatMap (encNatMap m) = .ok m := by
  induction m with
  | nil => rfl
  | cons p m ih => simp [encNatMap, decNatMap, numToNat_nat] at *; simp [ih, Except.map]

theorem find?_field (k k' : String) (b : Bool) (j : J) :
    (field k' b j).find? (fun p => decide (p.1 = k)) =
      if b = true ∧ k' = k then some (k', j) else none := by
  unfold field
  cases b <;> by_cases h : k' = k <;> simp [h]

theorem field_keys (k : String) (b : Bool) (j : J) : ∀ p ∈ field k b j, p.1 = k := by
  intro p hp
  unfold field at hp
  split at hp <;> simp_all

/-! ## Вариант стрелки -/

theorem dec_enc_variant (v : Variant) (h : extOK variantKeys v.ext) :
    decVariant (encVariant v) = .ok v := by
  obtain ⟨cond, to, resume, calls, ext⟩ := v
  simp only at h
  have hA : ∀ (A : Obj) k, k ∈ variantKeys → get (A ++ ext) k = get A k :=
    fun A k hk => get_append_ext h A hk
  have hU : ∀ (A : Obj), (∀ p ∈ A, p.1 ∈ variantKeys) → unknown variantKeys (A ++ ext) = ext :=
    fun A hAk => unknown_append_ext h A hAk
  simp only [encVariant, decVariant]
  rw [hA _ "if" (by simp [variantKeys]), hA _ "to" (by simp [variantKeys]),
    hA _ "resume" (by simp [variantKeys]), hA _ "do" (by simp [variantKeys])]
  rw [hU _ (by
    intro p hp
    simp only [List.mem_append, List.mem_singleton] at hp
    rcases hp with ((hp | rfl) | hp) | hp
    · rw [field_keys _ _ _ p hp]; simp [variantKeys]
    · simp [variantKeys]
    · rw [field_keys _ _ _ p hp]; simp [variantKeys]
    · rw [field_keys _ _ _ p hp]; simp [variantKeys])]
  cases cond <;> cases resume <;> cases calls <;>
    simp [field, get, decStrs_cons_map, bind, Except.bind, pure, Except.pure] <;> rfl

theorem dec_enc_variants (vs : List Variant) (h : ∀ v ∈ vs, extOK variantKeys v.ext) :
    decVariants (vs.map encVariant) = .ok vs := by
  induction vs with
  | nil => rfl
  | cons v vs ih =>
    have h1 := dec_enc_variant v (h v (by simp))
    have h2 := ih (fun w hw => h w (by simp [hw]))
    simp [decVariants, h1, h2, bind, Except.bind, pure, Except.pure]

theorem dec_enc_on (on : List (EventId × List Variant)) (h : OnWF on) :
    decOn (encOn on) = .ok on := by
  induction on with
  | nil => rfl
  | cons p on ih =>
    have h1 := dec_enc_variants p.2 (h p (by simp))
    have h2 := ih (fun q hq => h q (by simp [hq]))
    simp [encOn, decOn] at h2 ⊢
    simp [h1, h2, bind, Except.bind]
    rfl

/-! ## Узлы -/

theorem decChildrenIn_ext {ext : Obj} (h : extOK nodeKeys ext) :
    decChildrenIn ext = .ok none := by
  induction ext with
  | nil => simp [decChildrenIn]
  | cons p rest ih =>
    have hp : p.1 ≠ "children" := fun he => h p (by simp) (by simp [he, nodeKeys])
    have hr : extOK nodeKeys rest := fun q hq => h q (by simp [hq])
    obtain ⟨k, v⟩ := p
    simp only at hp
    rw [decChildrenIn.eq_def]
    simp only [hp, ↓reduceIte]
    exact ih hr

theorem decOn_cons_enc (p : EventId × List Variant) (on : List (EventId × List Variant))
    (h : OnWF (p :: on)) : decOn (encOn (p :: on)) = .ok (p :: on) := dec_enc_on _ h

mutual
theorem dec_enc_node : (n : NodeDoc) → NodeWF n → decNode (encNode n) = .ok n
  | .message on ts ext, h => by
    simp only [NodeWF] at h
    obtain ⟨hon, hext⟩ := h
    have e1 := find?_ext_none hext (k := "on") (by simp [nodeKeys])
    have e2 := find?_ext_none hext (k := "timers") (by simp [nodeKeys])
    have e3 := find?_ext_none hext (k := "initial") (by simp [nodeKeys])
    have hc := decChildrenIn_ext hext
    have hU := unknown_append_ext hext
      (field "on" (!on.isEmpty) (.obj (encOn on)) ++ field "timers" (!ts.isEmpty) (.obj (encNatMap ts)))
      (by
        intro p hp
        simp only [List.mem_append] at hp
        rcases hp with hp | hp
        · rw [field_keys _ _ _ p hp]; simp [nodeKeys]
        · rw [field_keys _ _ _ p hp]; simp [nodeKeys])
    simp only [encNode, decNode]
    rw [hU]
    cases on with
    | nil =>
      cases ts with
      | nil =>
        simp [field, decOnField, get, hc, e1, e2, e3, bind, Except.bind, pure,
          Except.pure]
      | cons t ts =>
        simp [field, decOnField, get, decChildrenIn, hc, e1, e3, bind, Except.bind, pure,
          Except.pure, decNatMap_enc]
    | cons q on =>
      have hq := decOn_cons_enc q on hon
      cases ts with
      | nil =>
        simp [field, decOnField, get, decChildrenIn, hc, e2, e3, bind, Except.bind, pure,
          Except.pure, hq]
      | cons t ts =>
        simp [field, decOnField, get, decChildrenIn, hc, e3, bind, Except.bind, pure,
          Except.pure, hq, decNatMap_enc]
  | .sec i cs on ext, h => by
    simp only [NodeWF] at h
    obtain ⟨hcs, hon, hext⟩ := h
    have ih := dec_enc_children cs hcs
    have e1 := find?_ext_none hext (k := "on") (by simp [nodeKeys])
    have e2 := find?_ext_none hext (k := "timers") (by simp [nodeKeys])
    have e3 := find?_ext_none hext (k := "initial") (by simp [nodeKeys])
    have hU := unknown_append_ext hext
      ([("initial", .str i)]
        ++ field "on" (!on.isEmpty) (.obj (encOn on))
        ++ [("children", .obj (encChildren cs))])
      (by
        intro p hp
        simp only [List.mem_append, List.mem_cons, List.mem_nil_iff, or_false] at hp
        rcases hp with (rfl | hp) | rfl
        · simp [nodeKeys]
        · rw [field_keys _ _ _ p hp]; simp [nodeKeys]
        · simp [nodeKeys])
    simp only [encNode, decNode]
    rw [hU]
    cases on with
    | nil =>
      simp [field, decOnField, get, decChildrenIn, ih, e1, e2, bind, Except.bind, pure,
        Except.pure, Except.map]
    | cons q on =>
      have hq := decOn_cons_enc q on hon
      simp [field, decOnField, get, decChildrenIn, ih, e2, bind, Except.bind, pure,
        Except.pure, Except.map, hq]
theorem dec_enc_children : (cs : List (NodeId × NodeDoc)) → ChildrenWF cs →
    decChildren (encChildren cs) = .ok cs
  | [], _ => by simp [encChildren, decChildren]
  | (k, n) :: rest, h => by
    simp only [ChildrenWF] at h
    have h1 := dec_enc_node n h.1
    have h2 := dec_enc_children rest h.2
    simp [encChildren, decChildren, h1, h2, bind, Except.bind, pure, Except.pure]
end

/-! ## Документ -/

theorem decodeRaw_encode (d : Doc) (h : DocWF d) : decodeRaw (encode d) = .ok d := by
  obtain ⟨req, conds, calls, vars, bot, ext⟩ := d
  obtain ⟨hreq, hbot, hsec, hext⟩ := h
  simp only at hreq hbot hsec hext
  have hn := dec_enc_node bot hbot
  have ek : ∀ k ∈ docKeys, ext.find? (fun p => decide (p.1 = k)) = none :=
    fun k hk => find?_ext_none hext hk
  have e1 := ek "figram" (by simp [docKeys])
  have e2 := ek "requires" (by simp [docKeys])
  have e3 := ek "conditions" (by simp [docKeys])
  have e4 := ek "calls" (by simp [docKeys])
  have e5 := ek "variables" (by simp [docKeys])
  have e6 := ek "bot" (by simp [docKeys])
  have hU := unknown_append_ext hext
    ([("figram", .num 1 0)]
      ++ field "requires" (!req.isEmpty) (.arr (req.map .str))
      ++ field "conditions" (!conds.isEmpty) (.obj (encStrMap conds))
      ++ field "calls" (!calls.isEmpty) (.obj (encStrMap calls))
      ++ field "variables" (!vars.isEmpty) (.obj (encStrMap vars))
      ++ [("bot", encNode bot)])
    (by
      intro p hp
      simp only [List.mem_append, List.mem_singleton] at hp
      rcases hp with ((((rfl | hp) | hp) | hp) | hp) | rfl
      · simp [docKeys]
      all_goals first
        | (rw [field_keys _ _ _ p hp]; simp [docKeys])
        | simp [docKeys])
  have hd : ∀ (k : String) (m : List (String × String)), k ∈ ["conditions", "calls", "variables"] →
      ∀ (A B : Obj), (∀ p ∈ A, p.1 ≠ k) → (∀ p ∈ B, p.1 ≠ k) →
      decDecl (A ++ field k (!m.isEmpty) (.obj (encStrMap m)) ++ B) k = .ok m := by
    intro k m _ A B hA hB
    have gA : A.find? (fun p => decide (p.1 = k)) = none :=
      List.find?_eq_none.mpr (fun p hp hpk => hA p hp (by simpa using hpk))
    have gB : B.find? (fun p => decide (p.1 = k)) = none :=
      List.find?_eq_none.mpr (fun p hp hpk => hB p hp (by simpa using hpk))
    cases m with
    | nil => simp [decDecl, get, field, List.find?_append, gA, gB, pure, Except.pure]
    | cons x xs =>
      simp [decDecl, get, field, List.find?_append, gA]
      exact decStrMap_enc (x :: xs)
  simp only [encode, decodeRaw]
  rw [hU]
  cases bot with
  | message => simp [NodeDoc.isSec] at hsec
  | sec i cs on bext =>
    have hc := hd "conditions" conds (by simp)
      ([("figram", .num 1 0)] ++ field "requires" (!req.isEmpty) (.arr (req.map .str)))
      (field "calls" (!calls.isEmpty) (.obj (encStrMap calls))
        ++ field "variables" (!vars.isEmpty) (.obj (encStrMap vars))
        ++ [("bot", encNode (.sec i cs on bext))] ++ ext)
      (by intro p hp; simp only [List.mem_append, List.mem_singleton] at hp
          rcases hp with rfl | hp
          · simp
          · rw [field_keys _ _ _ p hp]; simp)
      (by intro p hp; simp only [List.mem_append, List.mem_singleton] at hp
          rcases hp with ((hp | hp) | rfl) | hp
          · rw [field_keys _ _ _ p hp]; simp
          · rw [field_keys _ _ _ p hp]; simp
          · simp
          · intro he; exact hext p hp (by simp [he, docKeys]))
    have hl := hd "calls" calls (by simp)
      ([("figram", .num 1 0)] ++ field "requires" (!req.isEmpty) (.arr (req.map .str))
        ++ field "conditions" (!conds.isEmpty) (.obj (encStrMap conds)))
      (field "variables" (!vars.isEmpty) (.obj (encStrMap vars))
        ++ [("bot", encNode (.sec i cs on bext))] ++ ext)
      (by intro p hp; simp only [List.mem_append, List.mem_singleton] at hp
          rcases hp with (rfl | hp) | hp
          · simp
          · rw [field_keys _ _ _ p hp]; simp
          · rw [field_keys _ _ _ p hp]; simp)
      (by intro p hp; simp only [List.mem_append, List.mem_singleton] at hp
          rcases hp with (hp | rfl) | hp
          · rw [field_keys _ _ _ p hp]; simp
          · simp
          · intro he; exact hext p hp (by simp [he, docKeys]))
    have hv := hd "variables" vars (by simp)
      ([("figram", .num 1 0)] ++ field "requires" (!req.isEmpty) (.arr (req.map .str))
        ++ field "conditions" (!conds.isEmpty) (.obj (encStrMap conds))
        ++ field "calls" (!calls.isEmpty) (.obj (encStrMap calls)))
      ([("bot", encNode (.sec i cs on bext))] ++ ext)
      (by intro p hp; simp only [List.mem_append, List.mem_singleton] at hp
          rcases hp with ((rfl | hp) | hp) | hp
          · simp
          · rw [field_keys _ _ _ p hp]; simp
          · rw [field_keys _ _ _ p hp]; simp
          · rw [field_keys _ _ _ p hp]; simp)
      (by intro p hp; simp only [List.mem_append, List.mem_singleton] at hp
          rcases hp with rfl | hp
          · simp
          · intro he; exact hext p hp (by simp [he, docKeys]))
    simp only [List.append_assoc] at hc hl hv
    simp only [List.append_assoc]
    rw [hc, hl, hv]
    have h1 : numToNat 1 0 = some 1 := numToNat_nat 1
    cases req with
    | nil =>
      simp [get, List.find?_append, find?_field, e2, hn, h1, bind, Except.bind, pure,
        Except.pure]
    | cons r rs =>
      simp [get, List.find?_append, find?_field, e2, hn, h1, bind, Except.bind, pure,
        Except.pure, decStrs_cons_map]

/-- **Полнота и точность.** -/
theorem decode_encode (d : Doc) (h : DocWF d) : decode (encode d) = .ok d := by
  have hr := h.requires
  simp only [decode, decodeRaw_encode d h, bind, Except.bind]
  have : (d.requires.all fun f => decide (f ∈ knownFeatures)) = true := by
    simp only [List.all_eq_true, decide_eq_true_eq]; exact hr
  simp [this, pure, Except.pure]

/-- **Однозначность.** -/
theorem encode_injective (d₁ d₂ : Doc) (h₁ : DocWF d₁) (h₂ : DocWF d₂)
    (he : encode d₁ = encode d₂) : d₁ = d₂ := by
  have e1 := decode_encode d₁ h₁
  have e2 := decode_encode d₂ h₂
  rw [he] at e1
  rw [e1] at e2
  exact Except.ok.inj e2

/-- **Безопасность расширений.** -/
theorem decode_requires (j : J) (d : Doc) (h : decode j = .ok d) :
    ∀ f ∈ d.requires, f ∈ knownFeatures := by
  simp only [decode, bind, Except.bind] at h
  split at h
  · simp at h
  · rename_i d' _
    split at h
    · rename_i hall
      simp only [pure, Except.pure, Except.ok.injEq] at h
      subst h
      simpa using hall
    · simp [throw, throwThe, MonadExceptOf.throw] at h

/-! ## Что не влияет на поведение -/

def Variant.strip (v : Variant) : Variant := { v with ext := [] }

def stripOn (on : List (EventId × List Variant)) : List (EventId × List Variant) :=
  on.map fun p => (p.1, p.2.map Variant.strip)

mutual
def NodeDoc.strip : NodeDoc → NodeDoc
  | .message on ts _ => .message (stripOn on) ts []
  | .sec i cs on _ => .sec i (stripChildren cs) (stripOn on) []
def stripChildren : List (NodeId × NodeDoc) → List (NodeId × NodeDoc)
  | [] => []
  | (k, n) :: rest => (k, n.strip) :: stripChildren rest
end

/-- Документ без единого неизвестного ключа на любой глубине. -/
def Doc.strip (d : Doc) : Doc := { d with bot := d.bot.strip, ext := [] }

theorem onTrans_strip (id : NodeId) (on : List (EventId × List Variant)) :
    onTrans id (stripOn on) = onTrans id on := by
  simp only [onTrans, stripOn, List.map_map]
  apply List.map_congr_left
  intro p _
  simp only [Function.comp, List.map_map]
  rfl

mutual
theorem flat_strip (id : NodeId) (par : Option NodeId) :
    (n : NodeDoc) → flatNode id par n.strip = flatNode id par n
  | .message on ts ext => by
    simp only [NodeDoc.strip, flatNode, onTrans_strip]
  | .sec i cs on ext => by
    simp only [NodeDoc.strip, flatNode, onTrans_strip, flatChildren_strip id cs]
theorem flatChildren_strip (par : NodeId) :
    (cs : List (NodeId × NodeDoc)) → flatChildren par (stripChildren cs) = flatChildren par cs
  | [] => by simp [stripChildren, flatChildren]
  | (k, n) :: rest => by
    simp only [stripChildren, flatChildren, flat_strip k (some par) n,
      flatChildren_strip par rest]
end

/-- **Неизвестные ключи не влияют на поведение бота.** -/
theorem toChart_strip (d : Doc) : d.strip.toChart = d.toChart := by
  simp only [Doc.strip, Doc.toChart, flat_strip]

/-- **Описания не влияют на поведение бота.** -/
theorem toChart_decls (d : Doc) (conds calls vars : List (String × String)) :
    { d with conditions := conds, calls := calls, variables := vars }.toChart = d.toChart := rfl

/-! ## Проверка документа гарантирует запись и чтение -/

theorem extOKb_sound {known : List String} {ext : Obj} (h : extOKb known ext = true) :
    extOK known ext := by
  intro p hp
  have := List.all_eq_true.mp h p hp
  simpa using this

theorem onOKb_sound {on : List (EventId × List Variant)} (h : onOKb on = true) : OnWF on := by
  intro p hp v hv
  exact extOKb_sound (List.all_eq_true.mp (List.all_eq_true.mp h p hp) v hv)

mutual
theorem nodeOKb_sound : (n : NodeDoc) → nodeOKb n = true → NodeWF n
  | .message on _ ext, h => by
    simp only [nodeOKb, Bool.and_eq_true] at h
    exact ⟨onOKb_sound h.1, extOKb_sound h.2⟩
  | .sec _ cs on ext, h => by
    simp only [nodeOKb, Bool.and_eq_true] at h
    exact ⟨childrenOKb_sound cs h.1.1, onOKb_sound h.1.2, extOKb_sound h.2⟩
theorem childrenOKb_sound : (cs : List (NodeId × NodeDoc)) → childrenOKb cs = true → ChildrenWF cs
  | [], _ => trivial
  | (_, n) :: rest, h => by
    simp only [childrenOKb, Bool.and_eq_true] at h
    exact ⟨nodeOKb_sound n h.1, childrenOKb_sound rest h.2⟩
end

theorem wfOK_sound (d : Doc) (h : d.wfOK = true) : DocWF d := by
  simp only [Doc.wfOK, Bool.and_eq_true, List.all_eq_true, decide_eq_true_eq] at h
  obtain ⟨⟨⟨hreq, hbot⟩, hsec⟩, hext⟩ := h
  refine ⟨hreq, nodeOKb_sound _ hbot, ?_, extOKb_sound hext⟩
  cases hb : d.bot with
  | sec => rfl
  | message => rw [hb] at hsec; simp at hsec

theorem check_wfOK (d : Doc) (h : d.check = []) : d.wfOK = true := by
  cases hw : d.wfOK with
  | true => rfl
  | false =>
    simp only [Doc.check, hw] at h
    simp at h

/-- **Документ, прошедший проверку, записывается и читается обратно
в точности тем же.** -/
theorem check_roundtrip (d : Doc) (h : d.check = []) : decode (encode d) = .ok d :=
  decode_encode d (wfOK_sound d (check_wfOK d h))

end Figram.Format
