import json
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db.models import Count
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt

from .chatbot import (
    generate_farm_image,
    get_chat_stream,
    is_image_request,
    transcribe_audio,
)
from .ml.loader import load_bundle, predict_one
from .models import *

logger = logging.getLogger(__name__)


def home(request):
    total_users = User.objects.filter(is_staff=False).count()
    total_predictions = Prediction.objects.count()
    return render(request, "home.html", locals())


def signup_view(request):
    if request.method == "POST":
        name = request.POST.get("name")
        phone = request.POST.get("phone")
        email = request.POST.get("email")
        password = request.POST.get("password")
        confirm_password = request.POST.get("confirm_password")

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return redirect("signup")

        if not name or not phone or not email or not password:
            messages.error(request, "Please fill all required fields!")
            return redirect("signup")

        if len(password) < 6:
            messages.error(request, "Password should be at least 6 characters.")
            return redirect("signup")

        if User.objects.filter(username=email).exists():
            messages.error(request, "Account already exists with this email.")
            return redirect("signup")

        user = User.objects.create_user(username=email, password=password)

        if " " in name:
            first, last = name.split(" ", 1)
        else:
            first, last = name, ""

        user.first_name = first
        user.last_name = last
        user.save()

        UserProfile.objects.create(user=user, phone=phone)

        login(request, user)
        messages.success(request, "Account created successfully. Welcome!")
        return redirect("predict")

    return render(request, "signup.html")


@login_required
def predict_view(request):
    feature_order = load_bundle()["feature_cols"]
    result = None
    last_data = None

    if request.method == "POST":
        data = {}
        try:
            for c in feature_order:
                data[c] = float(request.POST.get(c))
        except (ValueError, TypeError):
            messages.error(request, "Please enter valid numeric values.")
            return redirect("predict")

        label = predict_one(data)

        Prediction.objects.create(
            user=request.user,
            N=data["N"],
            P=data["P"],
            K=data["K"],
            Temperature=data["temperature"],
            Humidity=data["humidity"],
            PH=data["ph"],
            Rainfall=data["rainfall"],
            predicted_label=label,
        )

        result = label
        last_data = data
        messages.success(request, f"Recommended Crop: {label}")

    return render(request, "predict.html", locals())


def logout_view(request):
    logout(request)
    messages.success(request, "Logout Successfully.")
    return redirect("login")


def login_view(request):
    if request.method == "POST":
        username = request.POST.get("email")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)

        if not user:
            messages.error(request, "Invalid Login Credentials.")
            return redirect("login")

        login(request, user)
        messages.success(request, "Logged in successfully!")
        return redirect("predict")

    return render(request, "login.html")


@login_required
def user_history_view(request):
    predictions = Prediction.objects.filter(user=request.user)
    return render(request, "history.html", locals())


@login_required
def user_delete_prediction(request, id):
    prediction = get_object_or_404(Prediction, id=id, user=request.user)
    prediction.delete()
    messages.success(request, "Entry Removed from history")
    return redirect("user_history")


@login_required
def profile_view(request):
    profile = UserProfile.objects.get(user=request.user)

    if request.method == "POST":
        name = request.POST.get("name")
        phone = request.POST.get("phone")

        if name:
            parts = name.split(" ", 1)
            request.user.first_name = parts[0]
            request.user.last_name = parts[1] if len(parts) > 1 else ""

        profile.phone = phone
        request.user.save()
        profile.save()
        messages.success(request, "Profile Updated.")

    full_name = request.user.get_full_name()
    return render(request, "profile.html", locals())


@login_required
def change_password_view(request):
    if request.method == "POST":
        current = request.POST.get("curr_password")
        new = request.POST.get("new_password")
        confirm = request.POST.get("confirm_password")

        if not request.user.check_password(current):
            messages.error(request, "Current Password is incorrect.")
            return redirect("change_password")

        if len(new) < 6:
            messages.error(request, "New Password must be at least 6 characters.")
            return redirect("change_password")

        if new != confirm:
            messages.error(request, "New Password do not match.")
            return redirect("change_password")

        request.user.set_password(new)
        request.user.save()

        user = authenticate(
            request, username=request.user.username, password=new
        )

        if user:
            login(request, user)
            messages.success(request, "Password changed successfully!")
            return redirect("change_password")

    return render(request, "change_password.html", locals())


def admin_login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)

        if not user:
            messages.error(request, "Invalid Login Credentials.")
            return redirect("admin_login")

        if not user.is_staff:
            messages.error(request, "You are not authorized for admin panel.")
            return redirect("admin_login")

        login(request, user)
        messages.success(request, "Logged in successfully!")
        return redirect("admin_dashboard")

    return render(request, "admin_login.html")


def is_staff(user):
    return user.is_authenticated and user.is_staff


@user_passes_test(is_staff, login_url="admin_login")
def admin_dashboard_view(request):
    total_users = User.objects.filter(is_staff=False).count()
    total_predictions = Prediction.objects.count()

    crop_qs = (
        Prediction.objects.values("predicted_label")
        .annotate(c=Count("id"))
        .order_by("-c")[:10]
    )

    crop_labels = [i["predicted_label"].title() for i in crop_qs]
    crop_counts = [i["c"] for i in crop_qs]

    today = timezone.localdate()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    day_labels = [d.strftime("%d %b") for d in days]
    day_count = [
        Prediction.objects.filter(created_at__date=d).count() for d in days
    ]

    context = {
        "total_users": total_users,
        "total_predictions": total_predictions,
        "crop_labels_json": json.dumps(crop_labels),
        "crop_counts_json": json.dumps(crop_counts),
        "day_labels_json": json.dumps(day_labels),
        "day_count_json": json.dumps(day_count),
    }

    return render(request, "admin_dashboard.html", context)


@user_passes_test(is_staff, login_url="admin_login")
def admin_users_view(request):
    users = User.objects.filter(is_staff=False)
    return render(request, "admin_users_view.html", {"users": users})


@user_passes_test(is_staff, login_url="admin_login")
def admin_user_delete(request, id):
    user = get_object_or_404(User, id=id)
    user.delete()
    messages.success(request, "User Deleted")
    return redirect("admin_users_view")


@user_passes_test(is_staff, login_url="admin_login")
def admin_view_predictions(request):
    qs = Prediction.objects.select_related("user")
    crop = request.GET.get("crop")
    start = request.GET.get("start")
    end = request.GET.get("end")

    if crop:
        qs = qs.filter(predicted_label__iexact=crop)

    d_start = parse_date(start) if start else None
    d_end = parse_date(end) if end else None

    if d_start:
        qs = qs.filter(created_at__date__gte=d_start)

    if d_end:
        qs = qs.filter(created_at__date__lte=d_end)

    crops = (
        Prediction.objects.order_by("predicted_label")
        .values_list("predicted_label", flat=True)
        .distinct()
    )

    context = {
        "qs": qs,
        "crops": crops,
        "current_crop": crop,
        "start": start,
        "end": end,
    }

    return render(request, "admin_view_predictions.html", context)


@user_passes_test(is_staff, login_url="admin_login")
def admin_delete_prediction(request, id):
    prediction = get_object_or_404(Prediction, id=id)
    prediction.delete()
    messages.success(request, "Prediction Deleted")
    return redirect("admin_view_predictions")


@user_passes_test(is_staff, login_url="admin_login")
def admin_logout_view(request):
    logout(request)
    messages.success(request, "Logout Successfully.")
    return redirect("admin_login")


@user_passes_test(is_staff, login_url="admin_login")
def admin_change_password(request):
    if request.method == "POST":
        current = request.POST.get("curr_password")
        new = request.POST.get("new_password")
        confirm = request.POST.get("confirm_password")

        if not request.user.check_password(current):
            messages.error(request, "Current Password is incorrect.")
            return redirect("admin_change_password")

        if len(new) < 6:
            messages.error(request, "New Password must be at least 6 characters.")
            return redirect("admin_change_password")

        if new != confirm:
            messages.error(request, "New Passwords do not match.")
            return redirect("admin_change_password")

        request.user.set_password(new)
        request.user.save()

        user = authenticate(
            request, username=request.user.username, password=new
        )

        if user:
            login(request, user)
            messages.success(request, "Admin Password changed successfully!")
            return redirect("admin_change_password")

    return render(request, "admin_change_password.html")


# =========================================================
# FARM AI CHATBOT VIEWS
# =========================================================


@csrf_exempt
def chatbot_view(request):
    if request.method != "POST":
        return StreamingHttpResponse(
            "Invalid request method.", status=405, content_type="text/plain"
        )

    msg = request.POST.get("message", "").strip()
    context = request.POST.get("context", "").strip()
    language = request.POST.get("language", "auto").strip().lower()

    if not msg:
        return StreamingHttpResponse(
            "Please type a question.",
            content_type="text/plain; charset=utf-8",
        )

    # Image Request
    if is_image_request(msg):
        try:
            image_base64 = generate_farm_image(msg, language)
            return JsonResponse({
                "type": "image",
                "image": image_base64,
                "mime_type": "image/png",
                "message": "Here is your agriculture image.",
            })
        except Exception as e:
            logger.exception("FarmAI image request failed: %s", e)
            return JsonResponse(
                {
                    "type": "error",
                    "message": "Sorry, I couldn't generate that image right now. Please try again.",
                },
                status=500,
            )

    # Normal Text Chat
    response = get_chat_stream(msg, context, language)

    streaming_response = StreamingHttpResponse(
        response, content_type="text/plain; charset=utf-8"
    )
    streaming_response["Cache-Control"] = "no-cache"
    streaming_response["X-Accel-Buffering"] = "no"

    return streaming_response


@csrf_exempt
def chatbot_voice(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "message": "Invalid request method."}, status=405
        )

    audio = request.FILES.get("audio")
    language = request.POST.get("language", "auto").strip()

    if not audio:
        return JsonResponse(
            {"success": False, "message": "No audio file received."}, status=400
        )

    if audio.size > 10 * 1024 * 1024:
        return JsonResponse(
            {
                "success": False,
                "message": "Voice recording is too large. Please record a shorter clip.",
            },
            status=400,
        )

    try:
        transcript = transcribe_audio(
            audio.read(),
            mime_type=audio.content_type or "audio/webm",
            language=language,
        )
        return JsonResponse({"success": True, "transcript": transcript})
    except Exception as exc:
        logger.exception("FarmAI voice transcription failed: %s", exc)
        return JsonResponse(
            {
                "success": False,
                "message": "Voice transcription failed. Please try again.",
            },
            status=500,
        )


@csrf_exempt
def chatbot_feedback(request):
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    feedback = request.POST.get("feedback", "").strip()

    if feedback not in ["up", "down"]:
        return JsonResponse({"success": False}, status=400)

    logger.info("FarmAI Feedback recorded: %s", feedback)
    return JsonResponse({"success": True, "message": "Feedback received"})

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .chatbot import text_to_speech_base64

@csrf_exempt
def chatbot_tts(request):
    if request.method == "POST":
        text = request.POST.get("text", "")
        language = request.POST.get("language", "hi-IN")
        audio_b64 = text_to_speech_base64(text, language)
        return JsonResponse({"audio": audio_b64})
    return JsonResponse({"error": "Invalid request"}, status=400)