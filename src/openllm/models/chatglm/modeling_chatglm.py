# Copyright 2023 BentoML Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import annotations

import typing as t

import bentoml
import openllm
import transformers


if t.TYPE_CHECKING:
    import torch
else:
    torch = openllm.utils.LazyLoader("torch", globals(), "torch")


class ChatGLM(openllm.LLM["transformers.PreTrainedModel", "transformers.PreTrainedTokenizerFast"]):
    __openllm_internal__ = True

    def llm_post_init(self):
        self.device = torch.device("cuda")

    def import_model(
        self,
        model_id: str,
        tag: bentoml.Tag,
        *model_args: t.Any,
        tokenizer_kwds: dict[str, t.Any],
        **attrs: t.Any,
    ) -> bentoml.Model:
        trust_remote_code = attrs.pop("trust_remote_code", True)
        return bentoml.transformers.save_model(
            tag,
            transformers.AutoModel.from_pretrained(model_id, trust_remote_code=trust_remote_code),
            custom_objects={
                "tokenizer": transformers.AutoTokenizer.from_pretrained(
                    model_id, trust_remote_code=trust_remote_code, **tokenizer_kwds
                )
            },
        )

    def sanitize_parameters(
        self,
        prompt: str,
        max_new_tokens: int | None = None,
        num_beams: int | None = None,
        top_p: float | None = None,
        temperature: float | None = None,
        chat_history: list[str] | None = None,
        use_default_prompt_template: bool = False,
        **attrs: t.Any,
    ) -> tuple[str, dict[str, t.Any], dict[str, t.Any]]:
        prompt_text = ""

        if use_default_prompt_template and chat_history is not None:
            for i, (old_query, response) in enumerate(chat_history):
                prompt_text += f"[Round {i}]\n问：{old_query}\n答：{response}\n"
            prompt_text += f"[Round {len(chat_history)}]\n问：{prompt}\n答："
        else:
            prompt_text = prompt

        postprocess_generate_kwargs = {"chat_history": chat_history if chat_history is not None else None}

        # NOTE: The rest of attrs should be kwargs for GenerationConfig
        generate_kwargs = {
            "max_new_tokens": max_new_tokens,
            "num_beams": num_beams,
            "top_p": top_p,
            "temperature": temperature,
            **attrs,
        }

        return prompt_text, generate_kwargs, postprocess_generate_kwargs

    def postprocess_generate(
        self,
        prompt: str,
        generation_result: tuple[str, list[tuple[str, str]]],
        *,
        chat_history: list[tuple[str, str]] | None = None,
        **attrs: t.Any,
    ):
        generated, history = generation_result
        if self.config.retain_history:
            assert chat_history is not None, "'retain_history' is True while there is no history provided."
            chat_history.extend(history)
        return generated

    def generate(self, prompt: str, **attrs: t.Any) -> tuple[str, list[tuple[str, str]]]:
        with torch.inference_mode():
            self.model.eval()

            # Only use half precision if the model is not yet quantized
            if self.config.use_half_precision:
                self.model.half()

            self.model.cuda()

            return self.model.chat(
                self.tokenizer,
                prompt,
                generation_config=self.config.model_construct_env(do_sample=True, **attrs).to_generation_config(),
            )
