
import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.filter.event_message_type import EventMessageType
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType


@register(
    "astrbot_plugin_anti_recall",
    "wangxinghuo",
    "防撤回插件，自动分析撤回消息并生成锐评",
    "1.0.0",
    "https://github.com/wangxinghuo/astrbot_plugin_anti_recall",
)
class AntiRecallPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        try:
            # 获取配置

            self.enabled = config.get("enabled", True)

            self.enable_ai_analysis = config.get("enable_ai_analysis", True)

            self.ai_comment_prompt = config.get(
                "ai_comment_prompt",
                "你是一个幽默风趣的评论家，请对以下撤回的内容进行锐评，语气要轻松幽默，不要太严肃。内容如下：",
            )

            self.enable_content_filter = config.get("enable_content_filter", True)

            self.ai_filter_prompt = config.get(
                "ai_filter_prompt",
                "你是一个内容审核专家，请判断以下内容是否包含违法违规信息。请只回答 '是' 或 '否'，不要有任何其他文字。内容如下：",
            )

            self.fixed_llm_provider = config.get("fixed_llm_provider", "")

            self.enable_context_analysis = config.get("enable_context_analysis", True)

            self.context_count = min(config.get("context_count", 10), 10)  # 最多10条

            self.enable_image_recall = config.get("enable_image_recall", True)

            self.enable_private_chat = config.get("enable_private_chat", False)

            self.enable_group_chat = config.get("enable_group_chat", True)

            self.show_sender_info = config.get("show_sender_info", True)

            self.comment_style = config.get("comment_style", "幽默风趣")

            self.max_cache_size = config.get("max_cache_size", 1000)

            # 消息缓存，用于存储消息内容以便撤回时获取

            self.message_cache = {}

            # 缓存统计

            self.cache_hits = 0

            self.cache_misses = 0

            logger.info(
                f"[防撤回插件] 插件已加载，启用状态: {self.enabled}, AI分析: {self.enable_ai_analysis}, 违规检测: {self.enable_content_filter}, 最大缓存: {self.max_cache_size}, 固定LLM提供商: {self.fixed_llm_provider or '使用当前会话'}, 图片撤回检测: {self.enable_image_recall}, 上下文分析: {self.enable_context_analysis}, 上下文数量: {self.context_count}"
            )

        except Exception as e:
            logger.error(f"[防撤回插件] 初始化失败: {e}")
            raise

    @filter.event_message_type(EventMessageType.ALL)
    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    async def on_message(self, event: AstrMessageEvent):
        """缓存所有消息内容"""
        if not self.enabled:
            return

        # 只缓存群聊消息
        if not event.get_group_id():
            return

        # 只在群聊中启用时才缓存
        if not self.enable_group_chat:
            return

        # 检查是否是机器人自己发送的消息
        if event.get_sender_id() == event.get_self_id():
            return

        try:
            message_id = str(event.message_obj.message_id)  # 转换为字符串以确保类型一致
            message_content = self._extract_message_content(event)
            sender_id = event.get_sender_id()
            sender_name = event.get_sender_name()
            group_id = event.get_group_id()

            # 检查是否为空消息
            if not message_content or message_content.strip() == "":
                logger.debug(f"[防撤回插件] 跳过空消息: message_id={message_id}")
                return

            # 缓存消息
            self.message_cache[message_id] = {
                "content": message_content,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "group_id": group_id,
                "timestamp": event.message_obj.timestamp,
                "message_type": self._get_message_type(event),
            }

            logger.info(
                f"[防撤回插件] 缓存消息: message_id={message_id} (type={type(message_id).__name__}), 发送者={sender_name}, 内容={message_content[:50]}, 群组={group_id}, 当前缓存数={len(self.message_cache)}"
            )

            # 检查缓存大小，超过限制时清理最旧的消息
            if len(self.message_cache) > self.max_cache_size:
                # 按时间戳排序，删除最旧的消息
                sorted_messages = sorted(
                    self.message_cache.items(), key=lambda x: x[1]["timestamp"]
                )
                messages_to_remove = len(self.message_cache) - self.max_cache_size
                for i in range(messages_to_remove):
                    del self.message_cache[sorted_messages[i][0]]
                logger.info(
                    f"[防撤回插件] 缓存超过限制，已清理 {messages_to_remove} 条旧消息"
                )

        except Exception as e:
            logger.error(f"[防撤回插件] 缓存消息失败: {e}", exc_info=True)

    @filter.event_message_type(EventMessageType.ALL)
    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    async def on_recall(self, event: AstrMessageEvent):
        """监听撤回事件"""
        if not self.enabled:
            return

        try:
            # 获取原始消息
            raw_message = getattr(event.message_obj, "raw_message", None)

            logger.debug(f"[防撤回插件] 收到事件: raw_message={raw_message}")

            if not raw_message or not isinstance(raw_message, dict):
                return

            # 检查是否是撤回事件
            if raw_message.get("post_type") != "notice":
                return

            notice_type = raw_message.get("notice_type")

            # 添加调试日志
            logger.info(
                f"[防撤回插件] 收到撤回事件: notice_type={notice_type}, message_id={raw_message.get('message_id')}, user_id={raw_message.get('user_id')}"
            )

            # 处理群消息撤回
            if notice_type == "group_recall":
                await self._handle_group_recall(event, raw_message)
            # 处理好友消息撤回
            elif notice_type == "friend_recall" and self.enable_private_chat:
                await self._handle_friend_recall(event, raw_message)

        except Exception as e:
            logger.error(f"[防撤回插件] 处理撤回事件失败: {e}", exc_info=True)

    async def _handle_group_recall(self, event: AstrMessageEvent, raw_message: dict):
        """处理群消息撤回"""
        try:
            message_id = str(
                raw_message.get("message_id")
            )  # 转换为字符串以确保类型一致
            user_id = raw_message.get("user_id")
            group_id = raw_message.get("group_id")
            operator_id = raw_message.get("operator_id", user_id)

            logger.info(
                f"[防撤回插件] 处理群消息撤回: message_id={message_id} (type={type(message_id).__name__}), user_id={user_id}, group_id={group_id}"
            )

            if not message_id or not group_id:
                logger.warning(
                    f"[防撤回插件] 撤回事件缺少必要参数: message_id={message_id}, group_id={group_id}"
                )
                return

            # 获取缓存的撤回消息
            recalled_message = self.message_cache.get(message_id)

            if not recalled_message:
                self.cache_misses += 1
                logger.warning(
                    f"[防撤回插件] 未找到撤回消息的缓存: {message_id} (缓存总数: {len(self.message_cache)}, 命中率: {self._get_cache_hit_rate()})"
                )
                logger.debug(
                    f"[防撤回插件] 当前缓存的消息ID: {list(self.message_cache.keys())}"
                )
                return

            self.cache_hits += 1
            logger.info(
                f"[防撤回插件] 找到撤回消息缓存: {message_id} (缓存总数: {len(self.message_cache)}, 命中率: {self._get_cache_hit_rate()})"
            )

            # 清理缓存
            del self.message_cache[message_id]

            # 检查是否是机器人自己撤回的消息
            if user_id == event.get_self_id():
                logger.debug("[防撤回插件] 机器人自己撤回的消息，不处理")
                return

            logger.info(
                f"[防撤回插件] 检测到撤回事件: 消息ID={message_id}, 发送者={recalled_message['sender_name']}, 群组={group_id}"
            )

            # 检查是否是图片消息，如果禁用了图片撤回检测则跳过
            if (
                not self.enable_image_recall
                and recalled_message["message_type"] == "图片"
            ):
                logger.info("[防撤回插件] 图片撤回检测已禁用，跳过处理")
                return

            # 检查内容是否违规
            if self.enable_content_filter and await self._is_content_blocked(
                recalled_message["content"], event
            ):
                logger.info("[防撤回插件] 撤回的内容被AI判定为违规，不发送")
                return

            # 生成消息内容
            message_chain = await self._build_recall_message(
                recalled_message, operator_id, event
            )

            if message_chain:
                # 发送到群聊（合并转发消息）
                session_id = event.unified_msg_origin
                await self.context.send_message(
                    session_id, MessageChain(chain=message_chain)
                )
                logger.info(f"[防撤回插件] 已发送撤回消息到群聊: {group_id}")

        except Exception as e:
            logger.error(f"[防撤回插件] 处理群消息撤回失败: {e}")

    async def _handle_friend_recall(self, event: AstrMessageEvent, raw_message: dict):
        """处理好友消息撤回"""
        try:
            message_id = str(
                raw_message.get("message_id")
            )  # 转换为字符串以确保类型一致
            user_id = raw_message.get("user_id")

            logger.info(
                f"[防撤回插件] 处理好友消息撤回: message_id={message_id} (type={type(message_id).__name__}), user_id={user_id}"
            )

            # 获取缓存的撤回消息
            recalled_message = self.message_cache.get(message_id)

            if not recalled_message:
                self.cache_misses += 1
                logger.warning(
                    f"[防撤回插件] 未找到撤回消息的缓存: {message_id} (缓存总数: {len(self.message_cache)}, 命中率: {self._get_cache_hit_rate()})"
                )
                logger.debug(
                    f"[防撤回插件] 当前缓存的消息ID: {list(self.message_cache.keys())}"
                )
                return

            self.cache_hits += 1
            logger.info(
                f"[防撤回插件] 找到撤回消息缓存: {message_id} (缓存总数: {len(self.message_cache)}, 命中率: {self._get_cache_hit_rate()})"
            )

            # 清理缓存
            del self.message_cache[message_id]

            # 检查是否是机器人自己撤回的消息
            if user_id == event.get_self_id():
                logger.debug("[防撤回插件] 机器人自己撤回的消息，不处理")
                return

            logger.info(
                f"[防撤回插件] 检测到好友消息撤回: 消息ID={message_id}, 发送者={recalled_message['sender_name']}"
            )

            # 检查内容是否违规
            if self.enable_content_filter and await self._is_content_blocked(
                recalled_message["content"], event
            ):
                logger.info("[防撤回插件] 撤回的内容被AI判定为违规，不发送")
                return

            # 生成消息内容
            message_chain = await self._build_recall_message(
                recalled_message, user_id, event
            )

            if message_chain:
                # 发送到私聊
                # 构建私聊的 session_id
                session_id = f"aiocqhttp:{MessageType.FRIEND_MESSAGE.value}:{user_id}"
                await self.context.send_message(
                    session_id, MessageChain(chain=message_chain)
                )
                logger.info(f"[防撤回插件] 已发送撤回消息到私聊: {user_id}")

        except Exception as e:
            logger.error(f"[防撤回插件] 处理好友消息撤回失败: {e}")

    async def _build_recall_message(
        self, recalled_message: dict, operator_id: str, event: AstrMessageEvent
    ):
        """构建撤回消息（合并转发格式）"""
        try:
            nodes = []

            # 获取发送者信息
            sender_id = recalled_message["sender_id"]
            sender_name = recalled_message["sender_name"]
            message_type = recalled_message["message_type"]
            content = recalled_message["content"]
            group_id = recalled_message["group_id"]

            # 第一个节点：撤回内容
            recall_chain = []
            recall_chain.append(Comp.Plain("🚫 检测到撤回消息！\n"))

            if self.show_sender_info:
                recall_chain.append(Comp.Plain(f"👤 发送者: {sender_name}\n"))

            recall_chain.append(Comp.Plain(f"📝 消息类型: {message_type}\n"))
            recall_chain.append(Comp.Plain("\n📄 撤回内容:\n"))
            recall_chain.append(Comp.Plain("─" * 30 + "\n"))

            if content:
                # 如果是图片，添加图片
                if message_type == "图片":
                    if content.startswith("http"):
                        recall_chain.append(Comp.Image.fromURL(content))
                    else:
                        recall_chain.append(Comp.Image.fromFileSystem(content))
                else:
                    recall_chain.append(Comp.Plain(content))
            else:
                recall_chain.append(Comp.Plain("[无法获取内容]"))

            recall_chain.append(Comp.Plain("\n" + "─" * 30))

            # 创建撤回内容节点
            recall_node = Comp.Node(
                uin=int(sender_id), name=sender_name, content=recall_chain
            )
            nodes.append(recall_node)

            # 第二个节点：AI 锐评
            if self.enable_ai_analysis and content:
                ai_comment = await self._generate_ai_comment(
                    content, event, group_id, recalled_message["timestamp"]
                )
                if ai_comment:
                    comment_chain = []
                    comment_chain.append(Comp.Plain("💬 AI 锐评:\n"))
                    comment_chain.append(Comp.Plain("─" * 30 + "\n"))
                    comment_chain.append(Comp.Plain(ai_comment))
                    comment_chain.append(Comp.Plain("\n" + "─" * 30))

                    # 创建 AI 锐评节点（使用机器人的 QQ 号）
                    bot_id = event.get_self_id()
                    comment_node = Comp.Node(
                        uin=int(bot_id), name="AI 锐评助手", content=comment_chain
                    )
                    nodes.append(comment_node)

            return nodes

        except Exception as e:
            logger.error(f"[防撤回插件] 构建撤回消息失败: {e}")
            return None

    def _extract_context_messages(self, group_id: str, recalled_timestamp: int) -> list:
        """提取撤回消息前的上下文消息（用于理解撤回的上下文）"""
        try:
            context_messages = []
            logger.info(
                f"[防撤回插件] 开始提取上下文消息: group_id={group_id}, recalled_timestamp={recalled_timestamp}, 缓存总数={len(self.message_cache)}"
            )

            # 打印缓存中该群组的所有消息（用于调试）
            group_messages = []
            for message_id, msg_data in self.message_cache.items():
                if msg_data["group_id"] == group_id:
                    group_messages.append(msg_data)

            logger.info(f"[防撤回插件] 缓存中该群组共有 {len(group_messages)} 条消息")
            for i, msg in enumerate(group_messages, 1):
                logger.info(
                    f"[防撤回插件] 缓存消息 {i}: 类型={msg['message_type']}, 时间戳={msg['timestamp']}, 内容={msg['content'][:30]}"
                )

            # 遍历缓存中的所有消息
            for message_id, msg_data in self.message_cache.items():
                # 只处理同一群组的消息
                if msg_data["group_id"] != group_id:
                    logger.debug(
                        f"[防撤回插件] 跳过不同群组的消息: {msg_data['group_id']} != {group_id}"
                    )
                    continue

                # 只处理文字消息或包含文字的消息（不包括纯图片、纯语音等）
                # _get_message_type 方法返回的是中文类型：'文本'、'提及'、'引用'
                if msg_data["message_type"] not in ["文本", "提及", "引用"]:
                    logger.debug(
                        f"[防撤回插件] 跳过非文本消息: {msg_data['message_type']}"
                    )
                    continue

                # 只处理撤回消息之前的消息（用于理解撤回的上下文）
                if msg_data["timestamp"] >= recalled_timestamp:
                    logger.debug(
                        f"[防撤回插件] 跳过撤回消息之后或同时的消息: {msg_data['timestamp']} >= {recalled_timestamp}"
                    )
                    continue

                # 添加到上下文列表
                context_messages.append(
                    {
                        "sender_name": msg_data["sender_name"],
                        "content": msg_data["content"],
                        "timestamp": msg_data["timestamp"],
                    }
                )
                logger.debug(
                    f"[防撤回插件] 添加上下文消息: {msg_data['sender_name']} - {msg_data['content'][:30]}"
                )

            # 按时间戳排序（从旧到新）
            context_messages.sort(key=lambda x: x["timestamp"])

            # 只取最后 N 条消息（撤回消息之前的 N 条消息）
            context_messages = (
                context_messages[-self.context_count :] if context_messages else []
            )

            logger.info(f"[防撤回插件] 提取到 {len(context_messages)} 条上下文消息")

            # 打印上下文内容用于调试
            if context_messages:
                for i, ctx in enumerate(context_messages, 1):
                    logger.info(
                        f"[防撤回插件] 上下文 {i}: {ctx['sender_name']} - {ctx['content']}"
                    )
            else:
                logger.warning("[防撤回插件] 未提取到上下文消息，可能原因：")
                logger.warning(
                    "[防撤回插件] 1. 缓存中该群组的消息类型不匹配（只提取文本、提及、引用）"
                )
                logger.warning("[防撤回插件] 2. 撤回消息之前没有符合条件的消息")

            return context_messages

        except Exception as e:
            logger.error(f"[防撤回插件] 提取上下文消息失败: {e}")
            return []

    async def _generate_ai_comment(
        self,
        content: str,
        event: AstrMessageEvent,
        group_id: str = None,
        recalled_timestamp: int = None,
    ):
        """生成 AI 锐评"""
        try:
            # 获取聊天模型 ID
            if self.fixed_llm_provider:
                provider_id = self.fixed_llm_provider
                logger.info(f"[防撤回插件] 使用固定的 LLM 提供商: {provider_id}")
            else:
                umo = event.unified_msg_origin
                provider_id = await self.context.get_current_chat_provider_id(umo=umo)
                logger.info(f"[防撤回插件] 使用当前会话的 LLM 提供商: {provider_id}")

            if not provider_id:
                logger.warning("[防撤回插件] 未获取到聊天模型 ID")
                return None

            # 提取上下文消息
            context_text = ""
            logger.info(
                f"[防撤回插件] 上下文分析参数: enable={self.enable_context_analysis}, group_id={group_id}, timestamp={recalled_timestamp}"
            )

            if self.enable_context_analysis and group_id and recalled_timestamp:
                context_messages = self._extract_context_messages(
                    group_id, recalled_timestamp
                )
                if context_messages:
                    context_text = "\n\n【撤回前的聊天上下文】\n"
                    context_text += "─" * 30 + "\n"
                    context_text += "以下是在撤回消息之前的聊天记录，可以帮助理解撤回的上下文和原因：\n"
                    for i, ctx in enumerate(context_messages, 1):
                        context_text += f"{i}. {ctx['sender_name']}: {ctx['content']}\n"
                    context_text += "─" * 30 + "\n"
                    logger.info(
                        f"[防撤回插件] 已添加 {len(context_messages)} 条上下文消息到提示词"
                    )
                else:
                    logger.warning("[防撤回插件] 未提取到上下文消息")
            else:
                logger.info("[防撤回插件] 上下文分析未启用或参数缺失")

            # 构建提示词
            style_prompts = {
                "幽默风趣": "你是一个幽默风趣的评论家，请对以下撤回的内容进行锐评，语气要轻松幽默，不要太严肃。如果有撤回前的聊天上下文，请结合上下文分析撤回的原因。",
                "严肃认真": "你是一个严肃认真的评论家，请对以下撤回的内容进行客观分析。如果有撤回前的聊天上下文，请结合上下文分析撤回的原因。",
                "毒舌吐槽": "你是一个毒舌的评论家，请对以下撤回的内容进行犀利吐槽。如果有撤回前的聊天上下文，请结合上下文吐槽。",
                "温和友善": "你是一个温和友善的评论家，请对以下撤回的内容进行温和点评。如果有撤回前的聊天上下文，请结合上下文点评。",
            }

            style_prompt = style_prompts.get(
                self.comment_style, style_prompts["幽默风趣"]
            )
            prompt = f"{style_prompt}\n{context_text}\n\n【撤回内容】\n{content}"

            logger.info(f"[防撤回插件] 开始生成 AI 锐评，内容: {content[:50]}...")
            logger.debug(f"[防撤回插件] 完整提示词: {prompt[:200]}...")

            # 调用 LLM 生成锐评
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
            )

            if llm_resp and llm_resp.completion_text:
                logger.info(
                    f"[防撤回插件] AI 锐评生成成功: {llm_resp.completion_text[:50]}..."
                )
                return llm_resp.completion_text
            else:
                logger.warning("[防撤回插件] AI 锐评生成失败: 无返回内容")
                return None

        except Exception as e:
            logger.error(f"[防撤回插件] 生成 AI 锐评失败: {e}")
            return None

    def _extract_message_content(self, event: AstrMessageEvent) -> str:
        """提取消息内容"""
        try:
            message_chain = event.message_obj.message
            content_parts = []

            for component in message_chain:
                # 处理文本消息
                if hasattr(component, "text"):
                    content_parts.append(component.text)
                # 处理图片消息
                elif hasattr(component, "url"):
                    content_parts.append(f"[图片: {component.url}]")
                elif hasattr(component, "file"):
                    content_parts.append(f"[图片: {component.file}]")
                # 处理其他类型
                elif hasattr(component, "type"):
                    content_parts.append(f"[{component.type}]")

            return "".join(content_parts)

        except Exception as e:
            logger.error(f"[防撤回插件] 提取消息内容失败: {e}")
            return ""

    def _get_message_type(self, event: AstrMessageEvent) -> str:
        """获取消息类型"""
        try:
            message_chain = event.message_obj.message

            if not message_chain:
                return "未知"

            # 检查是否包含特定类型的组件
            for component in message_chain:
                if hasattr(component, "type"):
                    # 将类型转换为字符串，确保匹配
                    component_type = str(component.type)
                    type_map = {
                        "plain": "文本",
                        "image": "图片",
                        "record": "语音",
                        "video": "视频",
                        "file": "文件",
                        "at": "提及",
                        "face": "表情",
                        "poke": "戳一戳",
                        "reply": "引用",
                    }
                    # 如果组件类型在映射中，返回对应类型
                    if component_type in type_map:
                        return type_map[component_type]

            # 如果没有找到已知类型，返回第一个组件的类型（转换为字符串）
            first_component = message_chain[0]
            if hasattr(first_component, "type"):
                component_type = str(first_component.type)
                # 再次尝试映射
                type_map = {
                    "plain": "文本",
                    "image": "图片",
                    "record": "语音",
                    "video": "视频",
                    "file": "文件",
                    "at": "提及",
                    "face": "表情",
                    "poke": "戳一戳",
                    "reply": "引用",
                }
                if component_type in type_map:
                    return type_map[component_type]
                return component_type

            return "未知"

        except Exception as e:
            logger.error(f"[防撤回插件] 获取消息类型失败: {e}")
            return "未知"

    async def _is_content_blocked(self, content: str, event: AstrMessageEvent) -> bool:
        """使用 AI 检查内容是否违规"""
        try:
            if not content:
                return False

            # 检查是否包含网址（防止危险参数导致封号）
            url_pattern = r"https?://[^\s]+|www\.[^\s]+"
            import re

            if re.search(url_pattern, content):
                logger.info(
                    f"[防撤回插件] 检测到撤回内容包含网址，已拦截: {content[:50]}..."
                )
                return True

            # 获取聊天模型 ID
            if self.fixed_llm_provider:
                provider_id = self.fixed_llm_provider
                logger.info(
                    f"[防撤回插件] 使用固定的 LLM 提供商进行违规检测: {provider_id}"
                )
            else:
                umo = event.unified_msg_origin
                provider_id = await self.context.get_current_chat_provider_id(umo=umo)
                logger.info(
                    f"[防撤回插件] 使用当前会话的 LLM 提供商进行违规检测: {provider_id}"
                )

            if not provider_id:
                logger.warning("[防撤回插件] 未获取到聊天模型 ID，跳过违规检测")
                return False

            # 构建提示词
            prompt = f"{self.ai_filter_prompt}\n\n{content}"

            # 调用 LLM 进行违规检测
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
            )

            # 检查返回结果
            result = llm_resp.completion_text.strip()
            is_blocked = "是" in result

            if is_blocked:
                logger.info(
                    f"[防撤回插件] AI 检测到违规内容: {content[:50]}... (检测结果: {result})"
                )
            else:
                logger.debug(
                    f"[防撤回插件] AI 检测通过: {content[:50]}... (检测结果: {result})"
                )

            return is_blocked

        except Exception as e:
            logger.error(f"[防撤回插件] AI 违规检测失败: {e}")
            # 如果 AI 检测失败，默认不拦截
            return False

    async def terminate(self):
        """插件卸载时清理资源"""
        self.message_cache.clear()
        logger.info(
            f"[防撤回插件] 插件已卸载，缓存已清理 (缓存命中率: {self._get_cache_hit_rate()})"
        )

    def _get_cache_hit_rate(self) -> str:
        """计算缓存命中率"""
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return "0%"
        return f"{(self.cache_hits / total * 100):.1f}%"

    @filter.command("防撤回状态", alias={"防撤回测试", "anti_recall_status"})
    async def anti_recall_status(self, event: AstrMessageEvent):
        """查看防撤回插件状态"""
        try:
            provider_info = (
                self.fixed_llm_provider if self.fixed_llm_provider else "使用当前会话"
            )
            # 统计各群组的缓存数量
            group_stats = {}
            for msg_id, msg_data in self.message_cache.items():
                group_id = msg_data.get("group_id", "unknown")
                if group_id not in group_stats:
                    group_stats[group_id] = 0
                group_stats[group_id] += 1

            group_info = "\n".join(
                [f"  群组 {gid}: {count} 条" for gid, count in group_stats.items()]
            )

            status_text = f"""🚫 防撤回插件状态
━━━━━━━━━━━━━━━━━━
✅ 启用状态: {"已启用" if self.enabled else "已禁用"}
🤖 AI分析: {"已启用" if self.enable_ai_analysis else "已禁用"}
🛡️ 违规检测: {"已启用" if self.enable_content_filter else "已禁用"}
🔧 LLM提供商: {provider_info}
📚 上下文分析: {"已启用" if self.enable_context_analysis else "已禁用"} ({self.context_count}条)
📸 图片撤回: {"已启用" if self.enable_image_recall else "已禁用"}
💬 群聊监听: {"已启用" if self.enable_group_chat else "已禁用"}
👤 私聊监听: {"已启用" if self.enable_private_chat else "已禁用"}
📝 显示发送者: {"已启用" if self.show_sender_info else "已禁用"}
🎭 锐评风格: {self.comment_style}
📊 缓存消息数: {len(self.message_cache)}
📈 缓存命中率: {self._get_cache_hit_rate()}
📁 群组分布:
{group_info}
━━━━━━━━━━━━━━━━━━"""

            yield event.plain_result(status_text)
        except Exception as e:
            logger.error(f"[防撤回插件] 查看状态失败: {e}")
            yield event.plain_result(f"查看状态失败: {e}")

    @filter.command("清空缓存", alias={"清理缓存", "clear_cache"})
    async def clear_cache(self, event: AstrMessageEvent):
        """清空消息缓存"""
        try:
            cache_size = len(self.message_cache)
            self.message_cache.clear()
            yield event.plain_result(f"✅ 已清空 {cache_size} 条缓存消息")
            logger.info(f"[防撤回插件] 用户 {event.get_sender_name()} 清空了缓存")
        except Exception as e:
            logger.error(f"[防撤回插件] 清空缓存失败: {e}")
            yield event.plain_result(f"清空缓存失败: {e}")

    @filter.command("缓存详情", alias={"cache_details", "show_cache"})
    async def show_cache_details(self, event: AstrMessageEvent):
        """显示缓存详情"""
        try:
            if not self.message_cache:
                yield event.plain_result("📋 缓存为空")
                return

            details = "📋 缓存详情 (最近20条):\n"
            details += "━━━━━━━━━━━━━━━━━━\n"

            # 按时间戳排序，显示最新的20条
            sorted_messages = sorted(
                self.message_cache.items(),
                key=lambda x: x[1]["timestamp"],
                reverse=True,
            )[:20]

            for msg_id, msg_data in sorted_messages:
                details += f"ID: {msg_id}\n"
                details += f"  发送者: {msg_data['sender_name']}\n"
                details += f"  群组: {msg_data['group_id']}\n"
                details += f"  内容: {msg_data['content'][:30]}...\n"
                details += f"  时间: {msg_data['timestamp']}\n"
                details += "─" * 30 + "\n"

            yield event.plain_result(details)
        except Exception as e:
            logger.error(f"[防撤回插件] 显示缓存详情失败: {e}")
            yield event.plain_result(f"显示缓存详情失败: {e}")
