"""Japanese business-language labels for simulation features."""

# Maps feature_name -> (label_ja, description_ja)
FEATURE_LABELS_JA: dict[str, tuple[str, str]] = {
    "log_days_since_friend_add": (
        "友だち追加からの経過日数",
        "友だち追加からの日数が長いほど、ブロック率が高くなる傾向があります",
    ),
    "past_open_rate": (
        "過去のメッセージ開封率",
        "過去のメッセージをよく開封するユーザーほど、今回も開封する可能性が高くなります",
    ),
    "past_click_rate": (
        "過去のクリック率",
        "過去にリンクをクリックした履歴があるユーザーほど、今回もクリックする可能性が高くなります",
    ),
    "is_high_engagement": (
        "高エンゲージメントユーザー",
        "積極的にメッセージに反応するアクティブユーザーの割合が予測に影響しています",
    ),
    "is_medium_engagement": (
        "中エンゲージメントユーザー",
        "平均的な反応を示すユーザー層の影響です",
    ),
    "is_low_engagement": (
        "低エンゲージメントユーザー",
        "反応が少ないユーザー層の割合が結果を押し下げています",
    ),
    "is_dormant": (
        "休眠ユーザー",
        "長期間反応のないユーザーが含まれることで、全体の効果が低下しています",
    ),
    "age_midpoint_norm": (
        "年齢層",
        "ターゲットセグメントの年齢分布が予測結果に影響しています",
    ),
    "is_rich_message": (
        "リッチメッセージ形式",
        "画像付きのリッチメッセージは、テキストのみのメッセージより開封率が高くなります",
    ),
    "is_image": (
        "画像メッセージ",
        "画像を含むメッセージは視覚的な訴求力が高く、開封・クリック率を向上させます",
    ),
    "is_evening": (
        "夕方配信",
        "18時〜22時の配信は、仕事終わりのユーザーの開封率が高い時間帯です",
    ),
    "is_morning": (
        "朝配信",
        "7時〜9時の通勤時間帯は、メッセージの確認率が比較的高くなります",
    ),
    "is_coupon_cta": (
        "クーポンCTA",
        "クーポン型のアクションボタンは、URL型よりもクリック率が高くなる傾向があります",
    ),
    "log_offer_value": (
        "特典金額",
        "特典の金額が高いほど、クリック率・転換率ともに向上する傾向があります",
    ),
    "is_discount": (
        "割引特典",
        "割引型の特典は、転換率への影響が特に大きくなります",
    ),
    "is_coupon_offer": (
        "クーポン特典",
        "クーポン型の特典は、割引と同様に転換率を大きく向上させます",
    ),
}


def get_label(feature_name: str) -> str:
    """Get the Japanese display label for a feature."""
    entry = FEATURE_LABELS_JA.get(feature_name)
    return entry[0] if entry else feature_name


def get_description(feature_name: str) -> str:
    """Get the Japanese description for a feature."""
    entry = FEATURE_LABELS_JA.get(feature_name)
    return entry[1] if entry else ""
