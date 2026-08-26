import discord
import httpx
from merchant.config import settings

MERCHANT_SERVER_URL = "http://localhost:8000"
APPROVER_USER_ID = int(settings.approver_discord_user_id)

intents = discord.Intents.default()
bot = discord.Client(intents=intents)


class OTPModal(discord.ui.Modal, title="Enter OTP to approve"):
    otp_input = discord.ui.TextInput(label="6-digit code", min_length=6, max_length=6)

    def __init__(self, checkout_id: str):
        super().__init__()
        self.checkout_id = checkout_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        resp = httpx.post(
            f"{MERCHANT_SERVER_URL}/internal/otp-verify",
            json={"checkout_id": self.checkout_id, "otp": self.otp_input.value},
            timeout=10.0,
        )
        if resp.status_code == 200:
            fingerprint = resp.json()["fingerprint"]
            await interaction.followup.send(
                f"Approved. Mandate fingerprint: `{fingerprint}`", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"Rejected: {resp.json().get('detail')}", ephemeral=True
            )


class ApprovalView(discord.ui.View):
    def __init__(self, checkout_id: str):
        super().__init__(timeout=300)
        self.checkout_id = checkout_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(OTPModal(self.checkout_id))


def send_approval_dm(
    checkout_id: str,
    items: list[dict],
    total_minor: int,
    delivery_address: str,
    otp: str,
    expires_at,
):
    embed = discord.Embed(title="OpenStore — Checkout Approval Needed", color=0xF1C40F)
    for item in items:
        embed.add_field(
            name=item["sku"],
            value=f"qty {item['qty']} @ {item['unit_minor']/100:.2f}",
            inline=False,
        )
    embed.add_field(name="Total", value=f"{total_minor/100:.2f}", inline=True)
    embed.add_field(name="Delivery address", value=delivery_address, inline=False)
    embed.add_field(name="Expires", value=expires_at.isoformat(), inline=True)
    embed.set_footer(text=f"checkout_id={checkout_id}")

    async def _send():
        user = await bot.fetch_user(APPROVER_USER_ID)
        await user.send(embed=embed, view=ApprovalView(checkout_id))
        await user.send(f"Your OTP: {otp}")

    bot.loop.create_task(_send())
