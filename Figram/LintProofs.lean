import Figram.Lint
import Figram.Correct

/-!
# Свойство проверки схемы

Отдельный модуль: проверка (`Figram.Lint`) входит в библиотеку для встраивания
и не зависит от модулей с доказательствами.
-/

namespace Figram
namespace Chart

/-- **Схема без ошибок компилируется в автомат, прошедший `wf`.** -/
theorem lint_nil_wf (c : Chart) (h : c.lint = []) : c.compile.wf = true := by
  rw [wfDirect_eq]
  unfold lint at h
  cases hw : c.wfDirect with
  | true => rfl
  | false =>
    exfalso
    simp only [hw, Bool.not_false, Bool.and_true] at h
    split at h
    · simp at h
    · rename_i hc
      rw [h] at hc
      simp at hc

end Chart
end Figram
